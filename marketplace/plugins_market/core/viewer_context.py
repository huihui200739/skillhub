"""列表 / 详情 / 下载等接口的访问者上下文（可选登录 + 是否可查看全部 Skill 审核状态）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from plugins_market.core.moderation import is_skill_moderation_publicly_visible
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.models.market_assets import MarketAssetDB


@dataclass(frozen=True, slots=True)
class ViewerContext:
    """来自 Authorization Bearer / X-System-Token（可选）。"""

    user_id: Optional[str]
    """GitCode 用户 id 或 system_admin_user。"""
    user_login: Optional[str]
    """GitCode login（与发布者展示名一致）；系统管理员 token 时为 system_admin_user。"""
    is_system_admin: bool
    """X-System-Token 校验通过。"""

    @property
    def is_market_moderation_admin(self) -> bool:
        return self.is_system_admin or is_market_moderation_username(self.user_login)

    @property
    def can_see_all_skill_moderation_states(self) -> bool:
        return self.is_market_moderation_admin

    def can_view_skill_asset(self, asset: MarketAssetDB) -> bool:
        if (asset.plugin_type or "").strip().lower() != "skill":
            return True
        if self.can_see_all_skill_moderation_states:
            return True
        if asset.publisher_id and self.user_id and asset.publisher_id == self.user_id:
            return True
        return is_skill_moderation_publicly_visible(getattr(asset, "moderation_status", None))

    def can_download_skill_asset(self, asset: MarketAssetDB) -> bool:
        return self.can_view_skill_asset(asset)


# ClawHub / 未登录列表等：仅可访问已审核通过的 Skill
ANONYMOUS_VIEWER = ViewerContext(user_id=None, user_login=None, is_system_admin=False)
