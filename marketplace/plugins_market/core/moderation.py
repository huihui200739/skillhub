# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill 上架审核：状态常量与可见性判断。"""

from __future__ import annotations

# 主表 market_assets.moderation_status
MODERATION_PENDING = "PENDING"
MODERATION_APPROVED = "APPROVED"
MODERATION_REJECTED = "REJECTED"


def moderation_coalesce_display(status: str | None) -> str:
    """空值视为已通过（兼容旧数据）。"""
    s = (status or "").strip()
    return s if s else MODERATION_APPROVED


def is_skill_moderation_publicly_visible(status: str | None) -> bool:
    return moderation_coalesce_display(status) == MODERATION_APPROVED
