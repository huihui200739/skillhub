# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""失败操作审计补录。

当变更类接口（POST/PUT/DELETE）返回 4xx/5xx 时，自动写一条 result='FAILED'
的审计记录，便于事后排查"明明操作了但没记录"的情况。

设计原则：
- 静默失败：审计写入本身不能影响主响应链路，所有异常吞掉只打日志。
- 端点反查：优先从 scope["endpoint"].__name__ 拿路由处理函数名，做名字→
  (event_type, action, resource_type) 映射；这比 URL 正则稳，路由前缀
  改了也不会失效。URL 正则只作 endpoint 不可用时的兜底。
- 事务隔离：audit_log 内部使用独立 SessionLocal，不污染主流程的 db。
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from fastapi import Request

from plugins_market.core.audit import audit_log
from plugins_market.core.audit_events import Action, EventType, ResourceType, Result
from plugins_market.core.context import get_audit_hint, get_user_id, get_user_name
from plugins_market.core.logging import get_logger


logger = get_logger(__name__)


# Handler 函数名 → (event_type, action, resource_type)。
# 函数名来自 routers/plugin.py 等的 async def，重命名时必须同步更新这里。
# 失败补录场景下我们没有 plugin_type 信息，无法区分 skill / plugin 子类，
# 统一归到 SKILL_MANAGE：对失败审计这一损失可接受（重点是别漏记）。
_ENDPOINT_AUDIT_MAP: dict[str, Tuple[str, str, str]] = {
    "publish_plugin": (EventType.SKILL_MANAGE, Action.PUBLISH, ResourceType.SKILL),
    "skill_import": (EventType.SKILL_MANAGE, Action.IMPORT, ResourceType.SKILL_BUNDLE),
    "create_git_source_and_sync_route": (
        EventType.SKILL_MANAGE, Action.GIT_SYNC, ResourceType.GIT_SOURCE,
    ),
    "sync_git_source_route": (
        EventType.SKILL_MANAGE, Action.GIT_SYNC, ResourceType.GIT_SOURCE,
    ),
    "delete_git_source_route": (
        EventType.SKILL_MANAGE, Action.GIT_SOURCE_DELETE, ResourceType.GIT_SOURCE,
    ),
    "moderate_skill": (EventType.SKILL_MODERATION, Action.MODERATE, ResourceType.SKILL),
    "delete_plugin_version": (EventType.SKILL_MANAGE, Action.DELETE, ResourceType.SKILL),
}


# URL 兜底规则：仅在 scope["endpoint"] 取不到时用（如 dispatch 错误、404 等）
_FALLBACK_ROUTE_MAP: list[tuple[str, re.Pattern[str], str, str, str]] = [
    ("POST", re.compile(r"/api/v1/plugins/skill-import/?"),
     EventType.SKILL_MANAGE, Action.IMPORT, ResourceType.SKILL_BUNDLE),
    ("POST", re.compile(r"/api/v1/plugins/git-sources/?"),
     EventType.SKILL_MANAGE, Action.GIT_SYNC, ResourceType.GIT_SOURCE),
    ("POST", re.compile(r"/api/v1/plugins/git-sources/[^/]+/sync/?"),
     EventType.SKILL_MANAGE, Action.GIT_SYNC, ResourceType.GIT_SOURCE),
    ("DELETE", re.compile(r"/api/v1/plugins/git-sources/[^/]+/?"),
     EventType.SKILL_MANAGE, Action.GIT_SOURCE_DELETE, ResourceType.GIT_SOURCE),
    ("POST", re.compile(r"/api/v1/plugins/[^/]+/moderation/?"),
     EventType.SKILL_MODERATION, Action.MODERATE, ResourceType.SKILL),
    ("DELETE", re.compile(r"/api/v1/plugins/[^/]+/versions/[^/]+/?"),
     EventType.SKILL_MANAGE, Action.DELETE, ResourceType.SKILL),
    ("POST", re.compile(r"/api/v1/plugins/?"),
     EventType.SKILL_MANAGE, Action.PUBLISH, ResourceType.SKILL),
]


# Path → resource_id / resource_version 解析仍走正则（endpoint name 拿不到 path 参数）
_RESOURCE_ID_PATTERNS = [
    re.compile(r"/api/v1/plugins/([^/]+)/versions/([^/]+)/?"),
    re.compile(r"/api/v1/plugins/([^/]+)/moderation/?"),
    re.compile(r"/api/v1/plugins/git-sources/([^/]+)(?:/sync)?/?"),
]


def _infer_audit_target_from_endpoint(request: Request) -> Optional[Tuple[str, str, str]]:
    """优先从 FastAPI route endpoint 函数名反查；拿不到返回 None。"""
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return None
    name = getattr(endpoint, "__name__", None)
    if not name:
        return None
    return _ENDPOINT_AUDIT_MAP.get(name)


def _infer_audit_target_from_path(method: str, path: str) -> Tuple[str, str, str]:
    """URL 正则兜底（endpoint name 缺失时）。"""
    for m, pat, et, act, rt in _FALLBACK_ROUTE_MAP:
        if m == method and pat.fullmatch(path):
            return et, act, rt
    return EventType.UNKNOWN, method, "unknown"


def _extract_resource_id(path: str) -> Optional[str]:
    for pat in _RESOURCE_ID_PATTERNS:
        m = pat.fullmatch(path)
        if m:
            return m.group(1)
    return None


def _extract_resource_version(path: str) -> Optional[str]:
    m = re.fullmatch(r"/api/v1/plugins/[^/]+/versions/([^/]+)/?", path)
    return m.group(1) if m else None


def _short_error(detail: Any) -> str:
    """把 HTTPException.detail 压成一行短文本。"""
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail[:500]
    if isinstance(detail, dict):
        msg = detail.get("message") or detail.get("detail") or ""
        if msg:
            return str(msg)[:500]
    return str(detail)[:500]


def _error_code(detail: Any, status_code: int) -> str:
    if isinstance(detail, dict):
        code = detail.get("error") or detail.get("code")
        if code:
            return str(code)
    return f"http_{status_code}"


def audit_failed_mutation(
    request: Request,
    status_code: int,
    detail: Any,
) -> None:
    """对变更类（POST/PUT/DELETE）的失败响应写一条审计。

    GET / HEAD / OPTIONS 直接忽略；参数校验失败（422）也忽略，因为不算实际操作。
    所有异常吞掉，绝不抛出。
    """
    try:
        method = (request.method or "").upper()
        if method not in ("POST", "PUT", "DELETE"):
            return
        if status_code < 400 or status_code == 422:
            return

        path = request.url.path or ""
        if not path.startswith("/api/v1/"):
            return
        # /api/v1/audit/* 自身的失败不补录（避免噪声）
        if path.startswith("/api/v1/audit/"):
            return

        # 优先按 endpoint 函数名反查；否则走 URL 正则；最后归为 UNKNOWN
        target = _infer_audit_target_from_endpoint(request)
        if target is None:
            target = _infer_audit_target_from_path(method, path)
        event_type, action, resource_type = target

        # framework 层（Depends/middleware）注入的业务标识。URL 提取不到时用作兜底
        hint = get_audit_hint() or {}
        resource_id = _extract_resource_id(path) or hint.get("resource_id")
        resource_version = _extract_resource_version(path) or hint.get("resource_version")
        resource_type = hint.get("resource_type", resource_type)  # hint 可覆盖推断的 resource_type
        action = hint.get("action", action)  # hint 可覆盖推断的 action（如 moderate 失败按 APPROVE/REJECT 记）
        # 413 早拒：未解析 form，resource_type 不可知
        if status_code == 413:
            resource_type = ""

        # 401 / 未登录场景 get_user_id() 返回 None，但 audit_logs.operator_id NOT NULL；
        # 兜底 "anonymous" 让"匿名尝试操作"也能落审计
        operator_id = get_user_id() or "anonymous"
        operator_name = get_user_name()  # 已登录用户的 username；未登录为 None
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        err_code = _error_code(detail, status_code)
        err_msg = _short_error(detail)

        # hint 中已被提到顶层的字段（resource_id/version/type/action）从 extra 里排除避免冗余
        hint_extra = {
        k: v for k, v in hint.items() if k not in ("resource_id", "resource_version", "resource_type", "action")
        }

        # audit_log 内部使用独立 SessionLocal，调用方无需管理 session
        audit_log(
            event_type=event_type,
            action=action,
            operator_id=operator_id,
            operator_name=operator_name,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            result=Result.FAILED,
            detail=f"{method} {path} 失败：{err_msg}" if err_msg else f"{method} {path} 失败",
            ip_address=client_ip,
            user_agent=user_agent,
            extra={
                "http_status": status_code,
                "error_code": err_code,
                "error_message": err_msg,
                "request_method": method,
                "request_path": path,
                **hint_extra,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_failed_mutation suppressed exception: %s", exc, exc_info=True)
