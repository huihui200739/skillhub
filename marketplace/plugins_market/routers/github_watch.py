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
from starlette.exceptions import HTTPException

from plugins_market.core.auth import get_oauth_user_id_and_login, normalize_oauth_provider_header
from plugins_market.core.cache import cache_get, cache_incr, cache_set_persistent
from plugins_market.core.config import settings
from plugins_market.core.errors import BusinessError, resolve_registered_error_metadata
from plugins_market.core.github_proxy import (
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

# 一键标星的目标仓库清单（openJiuwen-ai 组织下精选仓库）。
# 早期版本 repos 为空时会调 list_org_repos 拉取组织全部公开仓库（约 18 个），
# 现按业务要求固定为以下 10 个核心仓库，既聚焦核心项目又缩短标星耗时（≈13s）。
STAR_REPO_NAMES = (
    "jiuwenswarm",
    "agent-studio",
    "agent-core",
    "jiuwensymbiosis",
    "deepsearch",
    "agent-memory",
    "agent-protocol",
    "agent-core-java",
    "agent-runtime-java",
    "skillhub",
)

# 标星状态 Redis key：按 provider:login 隔离，永久（无 TTL）。
# 写入时机：标星请求成功（至少一个仓库 success）后；读取时机：GET /watch/status。
# Redis 不可用时 cache_get 返回 None、cache_set 静默跳过，降级为「未标星」，用户可重新点（PUT 幂等，无害）。
STAR_USER_KEY_PREFIX = "github_star_user:"


def _star_user_key(provider: str, login: str) -> str:
    return f"{STAR_USER_KEY_PREFIX}{provider}:{login}"


def _resolve_github_provider(x_oauth_provider: str | None) -> str:
    """解析 X-OAuth-Provider 头，缺失/非法时 fallback 为 "github"。

    本端点仅服务 GitHub 登录用户（前端 provider!=='github' 时隐藏按钮），
    故 token 归属一定是 github，fallback 必须用 "github" 以保证读写 key 一致。
    注意：normalize_oauth_provider_header(None) 返回 "gitcode"（app 默认），
    不符合本端点意图，故需显式判空；非法值（如 "gitlab"）抛 HTTPException(400)
    时也 fallback 为 "github"。
    """
    if x_oauth_provider and x_oauth_provider.strip():
        try:
            return normalize_oauth_provider_header(x_oauth_provider)
        except HTTPException:
            return "github"
    return "github"


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
    # repos 为空时表示「一键标星 openJiuwen-ai 组织核心仓库」（固定 10 个，见 STAR_REPO_NAMES）
    repos: list[WatchItem] = Field(default_factory=list, max_length=100)


@router.post("/watch", response_model=ResponseModel[dict])
async def star_repos(
    body: WatchBatchBody,
    authorization: str | None = Header(None),
    x_oauth_provider: str | None = Header(None, alias="X-OAuth-Provider"),
):
    """批量标星选中的仓库，返回逐个结果（success/failed）。

    鉴权：Authorization: Bearer <github token>。
    X-OAuth-Provider：标识 token 归属厂商（github/gitcode），用于标星成功后
    按用户隔离写入 Redis 状态；缺失/非法时 fallback 为 github（见 _resolve_github_provider，
    本端点仅服务 GitHub 用户，fallback 用 github 而非 app 默认 gitcode 以保证读写 key 一致）。
    """
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
        # repos 为空时，使用固定的核心仓库清单（STAR_REPO_NAMES），不再拉取组织全部仓库。
        # org 白名单：只允许标星 openJiuwen-ai 组织下的仓库，
        # 防止用户传入任意 owner/repo 使 SkillHub 沦为通用标星代理。
        items_to_star: list[WatchItem] = body.repos
        for item in items_to_star:
            if item.owner.lower() != WATCH_ORG.lower():
                _raise_with_failure_log(
                    "star github repos",
                    BusinessError(code=403, status_code=403, error="github_forbidden",
                                  message=f"仅支持标星 {WATCH_ORG} 组织下的仓库"),
                    org=WATCH_ORG,
                )
        if not items_to_star:
            items_to_star = [
                WatchItem(owner=WATCH_ORG, repo=name) for name in STAR_REPO_NAMES
            ]
        results: list[dict[str, Any]] = []
        success = 0
        failed = 0

        # 串行标星 + 请求间隔，遵循 GitHub 官方最佳实践：
        # https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
        #   "For PUT/POST/DELETE requests, wait at least one second between each request."
        #   "Make requests serially instead of concurrently."
        # 早期版本用 asyncio.gather 并发打多个 PUT（<1s 内全部发出），触发了 GitHub
        # 二级流控与反自动化标星系统：star 先被写入（204）随后被后台批量撤销，表现为
        # 「标星后马上能看到，过一段时间就没了」。串行 + ≥1s 间隔可避免该问题。
        # PUT /user/starred 是幂等的，已标星的再标一次返回 204，无需先查已标星列表。
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

        # GitHub 要求写请求间隔 ≥1s；取 1.25s 留余量。最后一个请求后不需等待。
        # 10 个仓库 ≈ 13s 完成，远低于前端 5min 超时，对 fire-and-forget 操作无感知影响。
        star_interval_sec = 1.25
        for idx, item in enumerate(items_to_star):
            if idx > 0:
                await asyncio.sleep(star_interval_sec)
            r = await _star_one(item)
            results.append(r)
            if r["status"] == "success":
                success += 1
            else:
                failed += 1
        elapsed = time.monotonic() - t0

        # 至少一个仓库标星成功，则写入按用户隔离的标星状态（Redis，跨设备同步）。
        # 全失败时不写，前端会回滚为未标星态，用户可重试。
        # 状态写入是 best-effort：标星已成功，不应因状态写入失败影响响应。
        # provider 解析：缺失/非法 fallback 为 "github"（见 _resolve_github_provider），
        # 与 GET /watch/status 读路径一致，避免读写 key 不匹配导致状态丢失。
        if success > 0:
            prov = _resolve_github_provider(x_oauth_provider)
            try:
                _, login = await get_oauth_user_id_and_login(token, prov)
                cache_set_persistent(_star_user_key(prov, login), "1")
            except Exception as e:
                logger.warning("github_watch write star status failed: %s", e)

        _log_completed(
            "star github repos",
            result="success",
            total=len(results), success=success, failed=failed,
            elapsed_ms=int(elapsed * 1000),
        )
        return ResponseModel(code=200, message="ok", data={"results": results})


@router.get("/watch/status", response_model=ResponseModel[dict])
async def get_watch_status(
    authorization: str | None = Header(None),
    x_oauth_provider: str | None = Header(None, alias="X-OAuth-Provider"),
):
    """查询当前用户是否已标星 openJiuwen-ai 组织仓库。

    返回 {starred: bool}。标星状态存 Redis（按 provider:login 隔离，永久 key），
    跨设备同步。Redis 不可用时返回 starred=false（降级，用户可重新点，PUT 幂等无害）。
    未登录 / token 无效返回 401。

    注：本端点是轻量查询，不进 operation_context，故功能关闭/token 失效时直接 raise
    而非走 star_repos 的 _raise_with_failure_log（那个会先记操作日志再抛）。
    """
    if not settings.github_star_enabled:
        raise BusinessError(code=404, status_code=404, error="feature_disabled",
                            message="标星功能已关闭")
    token = _extract_token(authorization)
    # 与 star_repos 一致：provider header 缺失/非法 fallback 为 "github"，
    # 保证读写 key 一致（见 _resolve_github_provider）。
    prov = _resolve_github_provider(x_oauth_provider)
    _, login = await get_oauth_user_id_and_login(token, prov)
    starred = cache_get(_star_user_key(prov, login)) == "1"
    return ResponseModel(code=200, message="ok", data={"starred": starred})
