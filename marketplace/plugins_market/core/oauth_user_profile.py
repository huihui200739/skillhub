"""按 OAuth 提供方拉取用户资料（GitCode / GitHub）。"""

from __future__ import annotations

from typing import Any

from plugins_market.core.github_user import fetch_github_profile
from plugins_market.core.gitcode_user import fetch_gitcode_profile


async def fetch_oauth_user_profile(provider: str, access_token: str) -> dict[str, Any] | None:
    if provider == "github":
        return await fetch_github_profile(access_token)
    return await fetch_gitcode_profile(access_token)
