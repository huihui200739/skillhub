# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
OAuth2 授权码模式：统一路由 /oauth/{gitcode|github}/...，不落库用户表。

GitCode 文档：https://docs.gitcode.com/docs/apis/
GitHub：https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
"""

from __future__ import annotations

import json
import logging
import secrets
from enum import Enum
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from plugins_market.core.auth import normalize_oauth_provider_header
from plugins_market.core.config import settings
from plugins_market.core.oauth_session_store import get_oauth_str_store
from plugins_market.core.oauth_user_profile import fetch_oauth_user_profile
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.schemas.common import ResponseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class OAuthProvider(str, Enum):
    gitcode = "gitcode"
    github = "github"


def _state_key(provider: OAuthProvider, state: str) -> str:
    return f"market_oauth_state:{provider.value}:{state}"


def _pending_key(provider: OAuthProvider, pending_id: str) -> str:
    return f"market_oauth_pending:{provider.value}:{pending_id}"


def _frontend_login_url() -> str:
    base = settings.oauth_frontend_origin.rstrip("/")
    return f"{base}/login"


def _redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{_frontend_login_url()}?oauth_error={quote(message, safe='')}",
        status_code=302,
    )


def _provider_label(p: OAuthProvider) -> str:
    return "GitHub" if p == OAuthProvider.github else "GitCode"


def _assert_oauth_ready(p: OAuthProvider) -> None:
    label = _provider_label(p)
    if p == OAuthProvider.gitcode:
        if not settings.gitcode_oauth_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} OAuth 未启用")
        if not settings.gitcode_oauth_client_id or not settings.gitcode_oauth_redirect_uri:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{label} OAuth 未正确配置")
    else:
        if not settings.github_oauth_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} OAuth 未启用")
        if not settings.github_oauth_client_id or not settings.github_oauth_redirect_uri:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{label} OAuth 未正确配置")


async def _exchange_code_for_token_json(client: httpx.AsyncClient, provider: OAuthProvider, code: str) -> dict:
    if provider == OAuthProvider.gitcode:
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
    else:
        token_res = await client.post(
            settings.github_oauth_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "redirect_uri": settings.github_oauth_redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    if token_res.status_code != 200:
        logger.warning(
            "%s token exchange failed: %s %s",
            _provider_label(provider),
            token_res.status_code,
            token_res.text,
        )
        raise ValueError("token_exchange_failed")
    return token_res.json()


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: OAuthProvider):
    """浏览器访问：重定向到对应厂商授权页。"""
    _assert_oauth_ready(provider)
    state = secrets.token_urlsafe(32)
    store = get_oauth_str_store()
    store.set_ex(_state_key(provider, state), "1", 600)

    if provider == OAuthProvider.gitcode:
        params = {
            "client_id": settings.gitcode_oauth_client_id,
            "redirect_uri": settings.gitcode_oauth_redirect_uri,
            "response_type": "code",
            "scope": settings.gitcode_oauth_scope,
            "state": state,
        }
        authorize_url = settings.gitcode_oauth_authorize_url
    else:
        params = {
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "response_type": "code",
            "scope": settings.github_oauth_scope,
            "state": state,
        }
        authorize_url = settings.github_oauth_authorize_url

    url = f"{authorize_url}?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: OAuthProvider,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """用 code 换 access_token，拉用户信息，写入一次性 oauth_session 后重定向前端 /login。"""
    label = _provider_label(provider)
    if provider == OAuthProvider.gitcode and not settings.gitcode_oauth_enabled:
        return _redirect_error(f"{label} OAuth 未启用")
    if provider == OAuthProvider.github and not settings.github_oauth_enabled:
        return _redirect_error(f"{label} OAuth 未启用")

    if error:
        msg = error_description or error
        return _redirect_error(msg or "授权已取消")

    if not code or not state:
        return _redirect_error("缺少授权参数")

    store = get_oauth_str_store()
    state_key = _state_key(provider, state)
    if not store.get(state_key):
        return _redirect_error("状态无效或已过期，请重新登录")
    store.delete(state_key)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                token_json = await _exchange_code_for_token_json(client, provider, code)
            except ValueError:
                return _redirect_error("换取访问令牌失败，请稍后重试")

            access_token = token_json.get("access_token")
            if not access_token:
                return _redirect_error(f"{label} 未返回 access_token")

            u = await fetch_oauth_user_profile(provider.value, access_token)
            if not u:
                logger.warning("%s user API failed or returned no profile", label)
                return _redirect_error(f"获取 {label} 用户信息失败")

            uid_raw = u.get("id") or ""
            uid = str(uid_raw).strip()
            if not uid:
                logger.warning("%s user API returned empty id", label)
                return _redirect_error(f"获取 {label} 用户信息失败")
            login = (u.get("login") or u.get("username") or "").strip() or str(uid_raw).strip()
            display_name = (u.get("name") or "").strip() or login
            avatar = u.get("avatar_url") or u.get("avatar") or ""

            result = {
                "provider": provider.value,
                "access_token": access_token,
                "token_type": str(token_json.get("token_type") or "bearer"),
                "user": {
                    "id": uid,
                    "name": display_name,
                    "login": login,
                    "avatar_url": (avatar or None) if avatar else None,
                },
            }

            pending = secrets.token_urlsafe(24)
            store.set_ex(
                _pending_key(provider, pending),
                json.dumps(result, ensure_ascii=False),
                120,
            )
            return RedirectResponse(
                url=(
                    f"{_frontend_login_url()}?"
                    f"oauth_session={quote(pending, safe='')}"
                    f"&oauth_provider={quote(provider.value, safe='')}"
                ),
                status_code=302,
            )
    except httpx.RequestError as e:
        logger.exception("%s OAuth request error: %s", label, e)
        return _redirect_error(f"连接 {label} 失败，请检查网络")
    except Exception as e:
        logger.exception("%s OAuth callback error: %s", label, e)
        return _redirect_error("登录处理失败")


class OAuthSessionBody(BaseModel):
    """一次性 pending id（由回调重定向的 oauth_session query 给出），勿用 GET 传参以免进访问日志。"""

    session: str = Field(..., min_length=8, max_length=256)


@router.post("/oauth/{provider}/session", response_model=ResponseModel[dict])
async def oauth_session_exchange(provider: OAuthProvider, body: OAuthSessionBody):
    """前端用 oauth_session 一次性兑换 access_token 与展示用用户信息（不返回 refresh_token）。"""
    _assert_oauth_ready(provider)
    store = get_oauth_str_store()
    key = _pending_key(provider, body.session)
    raw = store.get(key)
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="会话已过期或无效")
    store.delete(key)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="会话数据无效") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="会话数据无效")
    if data.get("provider") != provider.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="会话与登录渠道不一致")
    return ResponseModel(code=200, message="ok", data=data)


@router.get("/me", response_model=ResponseModel[dict])
async def auth_me(
    authorization: str | None = Header(None),
    x_oauth_provider: str | None = Header(None, alias="X-OAuth-Provider"),
):
    """校验当前 Bearer，按 X-OAuth-Provider 选择厂商用户接口（缺省 gitcode）。"""
    if not authorization or not authorization.strip().lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization")
    token = authorization[7:].strip()
    prov = normalize_oauth_provider_header(x_oauth_provider)
    profile = await fetch_oauth_user_profile(prov, token)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    gid = str(profile.get("id") or "").strip()
    if not gid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
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
