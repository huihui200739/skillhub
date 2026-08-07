# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
GitHub 仓库标星路由：代理转发到 api.github.com，供「一键标星 openjiuwen 仓库」使用。

注：路由路径 /github/watch 及类型名 Watch* 为历史命名（最初用 Watch/订阅 API，
后改为 Star/标星 API）。为兼容已发布的路径与前端调用，保留 watch 命名不改。

端点（挂载于 /api/v1/github）：
- POST /github/watch         批量标星选中的仓库，返回逐个结果

鉴权：从 Authorization: Bearer 头取用户 GitHub token（与 auth_me 一致）。
节流：转发层全局并发闸 + 写速率闸 + Retry-After 退避（见 core/github_proxy.py）。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import date
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from plugins_market.core.cache import cache_incr
from plugins_market.core.config import settings
from plugins_market.core.errors import BusinessError, resolve_registered_error_metadata
from plugins_market.core.github_proxy import (
    list_org_repos,
    star_repo,
)
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    bind_operation_actor,
    bind_operation_resource,
    complete_operation_result,
    is_invalid_or_denied_error,
    operation_context,
    operation_failure_result,
    operation_log_fields,
)
from plugins_market.schemas.common import ResponseModel

logger = get_logger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

WATCH_ORG = "openJiuwen-ai"


# ── 操作日志辅助（与 groups.py 三段式模式一致）──────────────
def _log_started(event: str, **fields: Any) -> None:
    logger.info(event, **operation_log_fields(stage="start", result="started", **fields))


def _log_completed(event: str, *, result: str = "success", **fields: Any) -> None:
    logger.info(event, **complete_operation_result(result=result, **fields))


def _raise_with_failure_log(event: str, error: Exception, **fields: Any):
    """记录失败操作日志后重新抛出异常（与 groups.py _raise_with_operation_failure_log 一致）。"""
    if isinstance(error, BusinessError):
        payload = error.detail
        error_code, error_class = resolve_registered_error_metadata(str(payload.get("error") or ""))
        if error_code and payload.get("error_code") is None:
            payload["error_code"] = error_code
        if error_class and payload.get("error_class") is None:
            payload["error_class"] = error_class
        result = operation_failure_result(payload)
        log_method = logger.info if is_invalid_or_denied_error(payload) else logger.warning
        log_method(
            event,
            **complete_operation_result(
                result=result.result,
                error_code=result.error_code,
                error_class=result.error_class,
                error_message=result.error_message,
                result_detail=result.result_detail,
                **fields,
            ),
        )
    with contextlib.suppress(Exception):
        setattr(error, "_operation_completion_logged", True)
    raise error


def _extract_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer token（与 oauth_provider.auth_me 一致）。"""
    if not authorization or not authorization.strip().lower().startswith("bearer "):
        raise BusinessError(
            code=401,
            status_code=401,
            error="auth_header_missing",
            message="Missing or invalid Authorization",
        )
    # 从 strip 后的字符串提取，与上面的校验基准一致（避免 " Bearer xxx" 等情况偏移错位）
    return authorization.strip()[7:].strip()


class WatchItem(BaseModel):
    # pattern 限制为 GitHub 合法字符（字母/数字/._-），防止路径注入拼出非预期 API 路径
    owner: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    repo: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class WatchBatchBody(BaseModel):
    # repos 为空时表示「一键标星全部 openJiuwen-ai 组织仓库」
    repos: list[WatchItem] = Field(default_factory=list, max_length=100)


@router.post("/watch", response_model=ResponseModel[dict])
async def star_repos(body: WatchBatchBody, authorization: str | None = Header(None)):
    """批量标星选中的仓库，返回逐个结果（success/failed）。"""
    with operation_context(operation_type="star github repos"):
        bind_operation_actor(actor_type="oauth_user")
        bind_operation_resource(resource_type="github_watch", resource_id=WATCH_ORG)
        _log_started("star github repos", org=WATCH_ORG)

        if not settings.github_star_enabled:
            _raise_with_failure_log(
                "star github repos",
                BusinessError(code=404, status_code=404, error="feature_disabled",
                              message="标星功能已关闭"),
                org=WATCH_ORG,
            )
        token = _extract_token(authorization)
        # 点击计数：总计数（永不过期）+ 每日计数（当天过期），Redis 不可用时静默跳过
        cache_incr("github_star_clicks:total")
        cache_incr(f"github_star_clicks:daily:{date.today().isoformat()}", ttl=86400)
        t0 = time.monotonic()
        # repos 为空时，自动拉取全部 openJiuwen-ai 组织仓库（一键标星全部）
        items_to_star: list[WatchItem] = body.repos
        # org 白名单：只允许标星 openJiuwen-ai 组织下的仓库，
        # 防止用户传入任意 owner/repo 使 SkillHub 沦为通用标星代理。
        # 自动拉取路径（repos 为空）的 owner 来自 list_org_repos(token, WATCH_ORG)，天然安全。
        for item in items_to_star:
            if item.owner.lower() != WATCH_ORG.lower():
                _raise_with_failure_log(
                    "star github repos",
                    BusinessError(code=403, status_code=403, error="github_forbidden",
                                  message=f"仅支持标星 {WATCH_ORG} 组织下的仓库"),
                    org=WATCH_ORG,
                )
        if not items_to_star:
            try:
                org_repos = await list_org_repos(token, WATCH_ORG)
                items_to_star = [
                    WatchItem(
                        owner=(r.get("owner") or {}).get("login", ""),
                        repo=r.get("name", ""),
                    )
                    for r in org_repos
                    if (r.get("owner") or {}).get("login") and r.get("name")
                ]
            except BusinessError as exc:
                _raise_with_failure_log("star github repos", exc, org=WATCH_ORG)
            except Exception as e:
                logger.warning("github_watch auto-list repos failed: %s", e)
                err = BusinessError(code=502, status_code=502, error="github_upstream_error",
                                    message="获取仓库列表失败")
                _raise_with_failure_log("star github repos", err, org=WATCH_ORG)
        t1 = time.monotonic()
        results: list[dict[str, Any]] = []
        success = 0
        failed = 0

        # 并发标星全部仓库（PUT /user/starred 是幂等的，已标星的再标一次返回 204，无需先查已标星列表）
        async def _star_one(item: WatchItem) -> dict[str, Any]:
            entry: dict[str, Any] = {"owner": item.owner, "repo": item.repo}
            try:
                await star_repo(token, item.owner, item.repo)
                entry["status"] = "success"
            except BusinessError as e:
                entry["status"] = "failed"
                entry["error"] = e.message
                entry["code"] = e.status_code
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e)
                entry["code"] = 502
            return entry

        put_results = await asyncio.gather(*[_star_one(item) for item in items_to_star])
        t2 = time.monotonic()
        for r in put_results:
            results.append(r)
            if r["status"] == "success":
                success += 1
            else:
                failed += 1

        _log_completed(
            "star github repos",
            result="success",
            total=len(results), success=success, failed=failed,
            list_ms=int((t1 - t0) * 1000), star_ms=int((t2 - t1) * 1000),
        )
        return ResponseModel(code=200, message="ok", data={"results": results})
