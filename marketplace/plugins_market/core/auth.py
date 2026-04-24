"""鉴权：Authorization Bearer 与 X-System-Token 二选一，不能同时传也不能都不传。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from common.security.security_utils import SecurityUtils
from plugins_market.core.config import settings
from plugins_market.core.context import set_user_id
from plugins_market.core.gitcode_user import fetch_gitcode_profile
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.core.viewer_context import ViewerContext

logger = logging.getLogger(__name__)


def _is_market_moderation_admin(*, is_system_admin: bool, acting_user_name: Optional[str]) -> bool:
    if is_system_admin:
        return True
    return is_market_moderation_username(acting_user_name)


@dataclass(frozen=True, slots=True)
class AuthContext:
    is_admin: bool
    acting_user_id: str
    acting_user_name: Optional[str] = None
    is_market_moderation_admin: bool = False
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


def _has_bearer(authorization: Optional[str]) -> bool:
    return bool(
        authorization
        and authorization.strip().lower().startswith("bearer ")
        and authorization[7:].strip()
    )


def _has_system_token(x_system_token: Optional[str]) -> bool:
    return bool(x_system_token is not None and x_system_token.strip())


def _resolved_system_admin_token() -> str:
    return SecurityUtils.get_decrypt_secret("SYSTEM_ADMIN_TOKEN", default="") or ""


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not _has_bearer(authorization):
        return None
    return authorization[7:].strip()


async def get_gitcode_user_id_and_login(token: str) -> tuple[str, str]:
    """返回 (GitCode 用户 id, 展示用发布者名)。

    发布者名与前端 Skill 打包逻辑一致：优先 ``login``，其次 ``username``，否则回退为 id。
    token 无效则抛 401。
    """
    profile = await fetch_gitcode_profile(token)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        )
    gid = str(profile["id"]).strip()
    if not gid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        )
    login = (profile.get("login") or profile.get("username") or "").strip() or gid
    return gid, login


async def get_gitcode_user_id(token: str) -> str:
    """返回 GitCode 用户 id（字符串）。token 无效则抛 401。"""
    uid, _ = await get_gitcode_user_id_and_login(token)
    return uid


async def require_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
) -> AuthContext:
    """
    接口鉴权：Authorization 与 X-System-Token 二选一。
    - Bearer：GitCode `/api/v5/user` 校验成功后，带 ``is_market_moderation_admin``（配置文件用户名命中时为 True）。
    - X-System-Token：与 `SYSTEM_ADMIN_TOKEN` 比对成功后，`is_admin=True` 且 ``is_market_moderation_admin=True``。
    """
    has_bearer = _has_bearer(authorization)
    has_system = _has_system_token(x_system_token)

    if has_bearer and has_system:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cannot use both Authorization and X-System-Token; provide exactly one",
        )
    if not has_bearer and not has_system:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization: provide Authorization: Bearer <token> or X-System-Token (exactly one)",
        )

    client_ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    if has_system:
        system_admin_token = _resolved_system_admin_token()
        if system_admin_token and x_system_token.strip() == system_admin_token:
            set_user_id(settings.system_admin_user)
            return AuthContext(
                is_admin=True,
                acting_user_id=settings.system_admin_user,
                acting_user_name=settings.system_admin_user,
                is_market_moderation_admin=True,
                ip_address=client_ip,
                user_agent=ua,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-System-Token",
        )

    token = _extract_bearer_token(authorization) or ""
    gitcode_user_id, gitcode_user_name = await get_gitcode_user_id_and_login(token)
    set_user_id(gitcode_user_id)
    return AuthContext(
        is_admin=False,
        acting_user_id=gitcode_user_id,
        acting_user_name=gitcode_user_name,
        is_market_moderation_admin=_is_market_moderation_admin(
            is_system_admin=False,
            acting_user_name=gitcode_user_name,
        ),
        ip_address=client_ip,
        user_agent=ua,
    )


async def resolve_viewer_context(
    _request: Request,
    authorization: Optional[str] = Header(None),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
) -> ViewerContext:
    """可选鉴权：无请求头、无效 token、或同时传两种凭证时视为匿名；否则解析用户并写入 context user_id。"""
    has_bearer = _has_bearer(authorization)
    has_system = _has_system_token(x_system_token)
    if has_bearer and has_system:
        return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    if has_system:
        system_admin_token = _resolved_system_admin_token()
        if system_admin_token and x_system_token.strip() == system_admin_token:
            u = settings.system_admin_user
            set_user_id(u)
            return ViewerContext(user_id=u, user_login=u, is_system_admin=True)
        return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    if has_bearer:
        token = _extract_bearer_token(authorization) or ""
        if not token:
            return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
        try:
            uid, login = await get_gitcode_user_id_and_login(token)
            set_user_id(uid)
            return ViewerContext(user_id=uid, user_login=login, is_system_admin=False)
        except HTTPException:
            return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    return ViewerContext(user_id=None, user_login=None, is_system_admin=False)


async def optional_auth(
    authorization: Optional[str] = Header(None),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
) -> None:
    if _has_system_token(x_system_token):
        system_admin_token = _resolved_system_admin_token()
        if system_admin_token and x_system_token.strip() == system_admin_token:
            set_user_id(settings.system_admin_user)
        return

    if _has_bearer(authorization):
        token = _extract_bearer_token(authorization) or ""
        if token:
            try:
                gitcode_user_id = await get_gitcode_user_id(token)
                set_user_id(gitcode_user_id)
            except HTTPException:
                pass
