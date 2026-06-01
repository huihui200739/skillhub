# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
GitCode OAuth2：授权后换取 access_token，不落库用户表；一次性 oauth_session 交给前端保存 GitCode token。

文档：https://docs.gitcode.com/docs/apis/
post-oauth-token-grant-type-authorization-code-code-code-client-id-client-id-client-secret-client-secret
"""

from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from plugins_market.core.config import settings
from plugins_market.core.gitcode_user import fetch_gitcode_profile
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.core.errors import auth_error_payload, http_error_payload
from plugins_market.core.oauth_session_store import get_oauth_str_store
from plugins_market.schemas.common import ResponseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_STATE_PREFIX = "market_oauth_gitcode_state:"
_PENDING_PREFIX = "market_oauth_gitcode_pending:"


def _http_exception(status_code: int, message: str, *, error: str, error_code: str | None = None) -> HTTPException:
    payload = (
        auth_error_payload(status_code=status_code, message=message, error=error, error_code=error_code)
        if status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        else http_error_payload(status_code=status_code, message=message, error=error, error_code=error_code)
    )
    return HTTPException(status_code=status_code, detail=payload)


def _frontend_login_url() -> str:
    base = settings.oauth_frontend_origin.rstrip("/")
    return f"{base}/login"


def _redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{_frontend_login_url()}?oauth_error={quote(message, safe='')}",
        status_code=302,
    )


@router.get("/oauth/gitcode/start")
async def gitcode_oauth_start():
    """浏览器访问：重定向到 GitCode 授权页（若浏览器已在 gitcode.com 登录，通常会快速回调）。"""
    if not settings.gitcode_oauth_enabled:
        raise _http_exception(
            status.HTTP_404_NOT_FOUND,
            "GitCode OAuth 未启用",
            error="oauth_disabled",
            error_code="SKILLHUB_OAUTH_DISABLED",
        )
    if not settings.gitcode_oauth_client_id or not settings.gitcode_oauth_redirect_uri:
        raise _http_exception(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GitCode OAuth 未正确配置",
            error="oauth_not_configured",
            error_code="SKILLHUB_OAUTH_NOT_CONFIGURED",
        )

    state = secrets.token_urlsafe(32)
    store = get_oauth_str_store()
    store.set_ex(f"{_STATE_PREFIX}{state}", "1", 600)

    params = {
        "client_id": settings.gitcode_oauth_client_id,
        "redirect_uri": settings.gitcode_oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.gitcode_oauth_scope,
        "state": state,
    }
    url = f"{settings.gitcode_oauth_authorize_url}?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/gitcode/callback")
async def gitcode_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """用 code 换 GitCode access_token，拉用户信息，写入一次性 oauth_session 后重定向前端 /login。"""
    if not settings.gitcode_oauth_enabled:
        return _redirect_error("GitCode OAuth 未启用")

    if error:
        msg = error_description or error
        return _redirect_error(msg or "授权已取消")

    if not code or not state:
        return _redirect_error("缺少授权参数")

    store = get_oauth_str_store()
    state_key = f"{_STATE_PREFIX}{state}"
    if not store.get(state_key):
        return _redirect_error("状态无效或已过期，请重新登录")
    store.delete(state_key)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_res = await client.post(
                settings.gitcode_oauth_token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.gitcode_oauth_client_id,
                    "client_secret": settings.gitcode_oauth_client_secret,
                    "redirect_uri": settings.gitcode_oauth_redirect_uri,
                },
            )
            if token_res.status_code != 200:
                logger.warning("GitCode token exchange failed: status=%s provider=gitcode", token_res.status_code)
                return _redirect_error("换取访问令牌失败，请稍后重试")

            token_json = token_res.json()
            access_token = token_json.get("access_token")
            if not access_token:
                return _redirect_error("GitCode 未返回 access_token")

            u = await fetch_gitcode_profile(access_token)
            if not u:
                logger.warning("GitCode user API failed or returned no profile")
                return _redirect_error("获取 GitCode 用户信息失败")

            gitcode_id = u.get("id") or ""
            login = (u.get("login") or u.get("username") or "").strip() or gitcode_id
            display_name = (u.get("name") or "").strip() or login
            avatar = u.get("avatar_url") or u.get("avatar") or ""

            result = {
                "access_token": access_token,
                "token_type": str(token_json.get("token_type") or "bearer"),
                "user": {
                    "id": str(gitcode_id).strip(),
                    "name": display_name,
                    "login": login,
                    "avatar_url": (avatar or None) if avatar else None,
                },
            }

            pending = secrets.token_urlsafe(24)
            store.set_ex(
                f"{_PENDING_PREFIX}{pending}",
                json.dumps(result, ensure_ascii=False),
                120,
            )
            return RedirectResponse(
                url=f"{_frontend_login_url()}?oauth_session={quote(pending, safe='')}",
                status_code=302,
            )
    except httpx.RequestError as e:
        logger.exception("GitCode OAuth request error: %s", e)
        return _redirect_error("连接 GitCode 失败，请检查网络")
    except Exception as e:
        logger.exception("GitCode OAuth callback error: %s", e)
        return _redirect_error("登录处理失败")


class GitCodeOAuthSessionBody(BaseModel):
    """一次性 pending id（由回调重定向的 oauth_session query 给出），勿用 GET 传参以免进访问日志。"""

    session: str = Field(..., min_length=8, max_length=256)


@router.post("/oauth/gitcode/session", response_model=ResponseModel[dict])
async def gitcode_oauth_session(body: GitCodeOAuthSessionBody):
    """前端用 oauth_session 一次性兑换 GitCode access_token 与展示用用户信息（不返回 refresh_token）。

    使用 POST + JSON body，避免 session 出现在 GET query（反代日志、Referer）。
    """
    store = get_oauth_str_store()
    key = f"{_PENDING_PREFIX}{body.session}"
    raw = store.get(key)
    if not raw:
        raise _http_exception(
            status.HTTP_400_BAD_REQUEST,
            "会话已过期或无效",
            error="oauth_session_expired",
            error_code="SKILLHUB_OAUTH_SESSION_EXPIRED",
        )
    store.delete(key)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise _http_exception(
            status.HTTP_400_BAD_REQUEST,
            "会话数据无效",
            error="oauth_session_invalid",
            error_code="SKILLHUB_OAUTH_SESSION_INVALID",
        ) from None
    return ResponseModel(code=200, message="ok", data=data)


@router.get("/me", response_model=ResponseModel[dict])
async def auth_me(authorization: str | None = Header(None)):
    """校验当前 Bearer（GitCode access_token），返回 GitCode 用户 id / name / login（不落库）。"""
    if not authorization or not authorization.strip().lower().startswith("bearer "):
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid Authorization",
            error="auth_header_missing",
            error_code="SKILLHUB_AUTH_HEADER_MISSING",
        )
    token = authorization[7:].strip()
    profile = await fetch_gitcode_profile(token)
    if not profile:
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            error="auth_token_invalid",
            error_code="SKILLHUB_AUTH_TOKEN_INVALID",
        )
    gid = str(profile.get("id") or "").strip()
    login = (profile.get("login") or profile.get("username") or "").strip() or gid
    name = (profile.get("name") or "").strip() or login
    return ResponseModel(
        code=200,
        message="ok",
        data={
            "id": gid,
            "name": name,
            "login": login,
            "avatar_url": profile.get("avatar_url") or profile.get("avatar"),
            "is_market_moderation_admin": is_market_moderation_username(login),
        },
    )
