# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Map marketplace models to ClawHub CLI JSON shapes."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from plugins_market.schemas.plugin import PluginListItem, PluginVersionDetail


def explore_sort_to_marketplace(sort: Optional[str]) -> Tuple[str, bool]:
    """
    ClawHub `sort` query → marketplace `order_by` and `desc`.
    Default: updated desc (newest first).
    """
    s = (sort or "updated").strip().lower()
    if s == "downloads" or s in ("installscurrent", "installsalltime", "trending"):
        return "install_count", True
    if s == "stars":
        return "like_count", True
    return "update_time", True


def search_result_row(item: PluginListItem) -> dict[str, Any]:
    score = float(item.install_count or 0)
    return {
        "slug": item.asset_id,
        "displayName": item.display_name or item.name,
        "summary": item.short_desc,
        "version": item.latest_version,
        "score": score,
        "updatedAt": item.update_time or item.create_time,
    }


def _tags_latest(version: Optional[str]) -> dict[str, str]:
    return {"latest": version or ""}


def _stats_block(item: PluginListItem) -> dict[str, Any]:
    return {
        "installsAllTime": int(item.install_count or 0),
        "stars": int(item.like_count or 0),
    }


def explore_item(item: PluginListItem) -> dict[str, Any]:
    lv = None
    if item.latest_version:
        lv = {
            "version": item.latest_version,
            "createdAt": item.update_time or item.create_time or 0,
            "changelog": "",
            "license": None,
        }
    return {
        "slug": item.asset_id,
        "displayName": item.display_name or item.name,
        "summary": item.short_desc,
        "tags": _tags_latest(item.latest_version),
        "stats": _stats_block(item),
        "createdAt": item.create_time or 0,
        "updatedAt": item.update_time or item.create_time or 0,
        "latestVersion": lv,
    }


def skill_detail_bundle(
    item: PluginListItem,
    version_detail: PluginVersionDetail,
) -> dict[str, Any]:
    ver = version_detail.version
    return {
        "skill": {
            "slug": item.asset_id,
            "displayName": item.display_name or item.name,
            "summary": item.short_desc,
            "tags": _tags_latest(ver),
            "stats": _stats_block(item),
            "createdAt": item.create_time or 0,
            "updatedAt": item.update_time or item.create_time or 0,
        },
        "latestVersion": {
            "version": ver,
            "createdAt": item.update_time or item.create_time or 0,
            "changelog": (version_detail.changelog or "").strip(),
            "license": None,
        },
        "owner": {
            "handle": item.publisher_name or item.publisher_id,
            "displayName": item.publisher_name,
            "image": None,
        },
        "moderation": {
            "isSuspicious": False,
            "isMalwareBlocked": False,
            "verdict": "clean",
            "reasonCodes": [],
            "updatedAt": None,
            "engineVersion": None,
            "summary": None,
        },
    }


def skill_version_row(row: PluginVersionDetail, files: list[dict[str, Any]], *, created_ms: int) -> dict[str, Any]:
    return {
        "version": {
            "version": row.version,
            "createdAt": created_ms,
            "changelog": (row.changelog or "").strip(),
            "changelogSource": "user",
            "license": None,
            "files": files,
        },
        "skill": {
            "slug": row.asset_id,
            "displayName": row.display_name,
        },
    }


def version_list_item(version: str, changelog: str | None, created_ms: int) -> dict[str, Any]:
    return {
        "version": version,
        "createdAt": created_ms,
        "changelog": (changelog or "").strip(),
        "changelogSource": "user",
    }
