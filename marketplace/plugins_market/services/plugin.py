"""Plugin publish, validation, and conflict handling."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from urllib.parse import urlparse
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext
from plugins_market.core.audit import (
    EVENT_SKILL_MODERATION,
    audit_log,
    list_skill_moderation_audit_logs_for_operator,
)
from plugins_market.core.context import _BJ_TZ
from plugins_market.core.errors import PublishError
from plugins_market.core.moderation import (
    MODERATION_APPROVED,
    MODERATION_PENDING,
    MODERATION_REJECTED,
    moderation_coalesce_display,
)
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.core.s3_storage_client import S3StorageClient
from plugins_market.schemas.plugin import (
    PluginDownloadData,
    PluginListItem,
    PluginListQuery,
    PluginListResponse,
    PluginPublishResult,
    PluginVersionDeleteData,
    PluginVersionDetail,
    SkillModerationAuditListItem,
    SkillModerationAuditListResponse,
    SkillModerationResult,
)
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories import (
    MarketAssetRepository,
    MarketAssetVersionRepository,
    PluginFetchRecordRepository,
)
from plugins_market.services.site_notifications import (
    notify_publisher_skill_review_finished,
    notify_review_admins_new_skill_submission,
)
from plugins_market.core.config import settings
from plugins_market.retrieval.index_manager import get_index_manager
from plugins_market.retrieval.search import retrieval_search
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.constants import (
    MAX_FILE_SIZE,
    MARKET_ASSET_SHORT_DESC_MAX_LEN,
    RUNTIME_SKILL,
    VERSION_PATTERN,
)
from plugins_market.validation.icon_png_optimize import optimize_png_icon_bytes

logger = logging.getLogger(__name__)


def _strip_yaml_front_matter(markdown_text: str | None) -> str | None:
    """Remove leading YAML front matter block from markdown text."""
    if markdown_text is None:
        return None
    text = markdown_text.lstrip("\ufeff")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return markdown_text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "".join(lines[idx + 1:]).lstrip("\r\n")
    return markdown_text


def _detail_desc_for_display(plugin_type: str | None, detail_desc: str | None) -> str | None:
    if (plugin_type or "").lower() == RUNTIME_SKILL:
        return _strip_yaml_front_matter(detail_desc)
    return detail_desc
    

def _list_item_with_viewer_flag(item: PluginListItem, viewer: ViewerContext) -> PluginListItem:
    return item.model_copy(update={"viewer_is_market_moderation_admin": viewer.is_market_moderation_admin})


def _moderation_for_publish(*, user_id: str, plugin_type: str | None) -> tuple[str | None, str | None]:
    """非 skill 始终已通过；skill 由普通用户发布为审核中，系统管理员发布为通过。"""
    pt = (plugin_type or "").strip().lower()
    if pt != "skill":
        return MODERATION_APPROVED, None
    if (user_id or "").strip() == (settings.system_admin_user or "").strip():
        return MODERATION_APPROVED, None
    return MODERATION_PENDING, None


def _apply_skill_asset_aggregate_from_versions(db: Session, asset_id: str) -> None:
    """
    按版本行重算 Skill 的 market_assets 聚合：moderation_status、moderation_reject_reason、
    public_latest_version。非 skill 则视为已通过，public_latest 跟随 latest。
    调用方在事务内执行；不 commit。
    """
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        return
    if (asset.plugin_type or "").lower() != "skill":
        asset.moderation_status = MODERATION_APPROVED
        asset.moderation_reject_reason = None
        asset.public_latest_version = asset.latest_version
        db.add(asset)
        return

    versions = version_repo.list_versions_chronological(asset_id)
    any_approved = False
    any_pending = False
    public_row: MarketAssetVersionDB | None = None
    latest_rejected: MarketAssetVersionDB | None = None

    for v in versions:
        ms = moderation_coalesce_display(getattr(v, "moderation_status", None))
        if ms == MODERATION_APPROVED:
            any_approved = True
            if public_row is None:
                public_row = v
            else:
                ct = v.create_time or 0
                pct = public_row.create_time or 0
                if ct > pct or (ct == pct and (v.version or "") > (public_row.version or "")):
                    public_row = v
        elif ms == MODERATION_PENDING:
            any_pending = True
        elif ms == MODERATION_REJECTED:
            if latest_rejected is None or (v.create_time or 0) > (latest_rejected.create_time or 0):
                latest_rejected = v

    if any_approved:
        asset.moderation_status = MODERATION_APPROVED
        asset.moderation_reject_reason = None
    elif any_pending:
        asset.moderation_status = MODERATION_PENDING
        asset.moderation_reject_reason = None
    else:
        asset.moderation_status = MODERATION_REJECTED
        asset.moderation_reject_reason = (
            (latest_rejected.moderation_reject_reason or None) if latest_rejected else None
        )

    asset.public_latest_version = (public_row.version if public_row else None)
    db.add(asset)


def _normalize_version(version: str) -> str:
    """Normalize surrounding whitespace only; do not rewrite semantic content."""
    return version.strip()


def _validate_version(version: str) -> None:
    """Ensure version matches <major>.<minor>.<patch> (no v prefix)."""
    if not VERSION_PATTERN.match(version):
        raise PublishError(
            code=422,
            error="manifest_validation_failed",
            message=("版本号格式错误，必须为 <主版本号>.<次版本号>.<修订号>，" "例如 1.0.0、1.0.1（不应有 v 前缀）"),
        )


def _storage_root(plugin_type: str | None) -> str:
    """Top-level OBS prefix: skills for skill type, plugins for everything else."""
    return "skills" if (plugin_type or "").lower() == "skill" else "plugins"


def _version_dir_prefix(publisher_id: str, asset_id: str, version: str, plugin_type: str | None = None) -> str:
    """Version directory key prefix: {root}/{publisher_id}/{asset_id}/{version}/"""
    root = _storage_root(plugin_type)
    return f"{root}/{publisher_id}/{asset_id}/{version}/"


def _build_storage_path(
    *,
    publisher_id: str,
    asset_id: str,
    version: str,
    asset_name: str,
    plugin_type: str | None = None,
) -> str:
    """Build object-key for zip: {root}/{publisher_id}/{asset_id}/{version}/{name}_{version}.zip"""
    prefix = _version_dir_prefix(publisher_id, asset_id, version, plugin_type)
    safe_name = asset_name.strip().replace(" ", "-")
    return f"{prefix}{safe_name}_{version}.zip"


def _compute_checksum(content: bytes) -> str:
    """SHA256 of content (for future client checksum comparison)."""
    return hashlib.sha256(content).hexdigest()


def _publish_idempotent_same_artifact(
    existing_version: MarketAssetVersionDB | None,
    computed_sha256: str,
) -> bool:
    """同一 asset + version 且库内已记录相同子包 SHA-256 时跳过写存储（幂等重试）。"""
    if existing_version is None:
        return False
    if (existing_version.status or "").upper() != "ACTIVE":
        return False
    stored = (existing_version.artifact_sha256 or "").strip()
    if not stored:
        return False
    return stored.lower() == computed_sha256.lower()


def _make_publish_result(
    asset: MarketAssetDB,
    version_row: MarketAssetVersionDB,
    zip_key: str,
) -> PluginPublishResult:
    ts_ms = version_row.create_time or asset.create_time or 0
    published_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    return PluginPublishResult(
        plugin_id=asset.asset_id,
        name=asset.name,
        version=version_row.version,
        status=version_row.status or "ACTIVE",
        published_at=published_at,
        storage_url=zip_key,
        plugin_type=asset.plugin_type,
    )


def _semver_sort_key(version: str | None) -> tuple[int, int, int]:
    """Parse x.y.z for ordering; invalid/missing sorts last."""
    v = (version or "").strip()
    if not v or not VERSION_PATTERN.match(v):
        return (-1, -1, -1)
    a, b, c = v.split(".", 2)
    return (int(a), int(b), int(c))


def _render_cumulative_changelog_file(versions: list[MarketAssetVersionDB]) -> str:
    """
    Build UTF-8 text for changelog.log: all historical version rows.
    Order by semver descending (largest version first).
    Plain style: [version] then blank line then changelog (same as API version_desc).
    """
    if not versions:
        return "（暂无版本记录）\n"

    ordered = sorted(
        versions,
        key=lambda r: _semver_sort_key(r.version),
        reverse=True,
    )
    blocks: list[str] = []
    for row in ordered:
        ver = (row.version or "").strip() or "未知"
        body = (row.changelog or "").strip() or "（无变更说明）"
        blocks.append(f"[{ver}]\n\n{body}")

    return "\n\n".join(blocks) + "\n"


def _is_uk_publisher_name_error(exc: IntegrityError) -> bool:
    msg = str(getattr(exc, "orig", None) or exc)
    low = msg.lower()
    return "uk_publisher_name" in low or ("unique" in low and "publisher_id" in low and "name" in low)


def _is_uk_asset_version_error(exc: IntegrityError) -> bool:
    msg = str(getattr(exc, "orig", None) or exc)
    low = msg.lower()
    return "uk_asset_version" in low or ("unique" in low and "asset_id" in low and "version" in low)


def publish(
    *,
    user_id: str,
    content: bytes,
    filename: str | None,
    expected_checksum: str,
    plugin_id: str | None,
    plugin_version: str | None,
    version_desc: str | None,
    force: bool,
    db: Session,
    storage: S3StorageClient,
    publisher_name_override: str | None = None,
) -> PluginPublishResult:
    """Validate, resolve conflicts, upload to S3, write asset/version, return result. Raises PublishError on failure."""
    if not filename or not filename.lower().endswith(".zip"):
        raise PublishError(
            code=400,
            error="invalid_file_format",
            message="仅支持 .zip 格式的插件包文件",
        )

    if len(content) > MAX_FILE_SIZE:
        raise PublishError(
            code=413,
            error="file_too_large",
            message="文件大小超过限制（最大512MB）",
        )

    computed = _compute_checksum(content)
    if computed != expected_checksum.lower():
        raise PublishError(
            code=400,
            error="checksum_mismatch",
            message="文件校验和不匹配，文件可能在传输过程中损坏",
        )

    if len(content) < 2 or content[:2] != b"PK":
        raise PublishError(
            code=400,
            error="invalid_file_format",
            message="仅支持 .zip 格式的插件包文件",
        )

    meta = extract_plugin_metadata(content)
    content_size = len(content)
    name = (meta["name"] or "").strip()
    display_name = (meta.get("display_name") or "").strip()
    manifest_version = (meta["version"] or "").strip()

    if not name:
        raise PublishError(
            code=400,
            error="invalid_plugin_config",
            message="plugin.yaml 配置文件格式错误或缺失：缺少必需的 name 字段",
        )

    if plugin_version is None:
        if not manifest_version:
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message="plugin.yaml 配置文件格式错误或缺失：缺少必需的 version 字段",
            )
        version = _normalize_version(manifest_version)
    else:
        version = _normalize_version(plugin_version)

    _validate_version(version)

    short_desc = meta.get("short_desc")
    if isinstance(short_desc, str) and len(short_desc) > MARKET_ASSET_SHORT_DESC_MAX_LEN:
        short_desc = short_desc[:MARKET_ASSET_SHORT_DESC_MAX_LEN]
    detail_desc = meta.get("detail_desc")
    tags = meta.get("tags") or []
    raw_publisher_name = meta.get("publisher_name") or ""
    plugin_type = meta.get("plugin_type")
    rt = (plugin_type or "").strip().lower() if isinstance(plugin_type, str) else ""
    # Bearer 发布时，市场展示发布者应优先使用当前登录用户身份，而不是包内 metadata.author/publisher_name。
    if publisher_name_override is not None:
        publisher_name = publisher_name_override.strip() or raw_publisher_name
    else:
        publisher_name = raw_publisher_name
    icon_bytes = meta.get("icon_bytes") or b""
    if icon_bytes:
        icon_bytes = optimize_png_icon_bytes(icon_bytes)

    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    pid = (plugin_id or "").strip()
    if pid:
        existing_asset = asset_repo.get_by_asset_id(pid)
        if not existing_asset:
            raise PublishError(
                code=404,
                error="plugin_not_found",
                message=f"插件 '{pid}' 不存在，无法添加新版本",
            )
        if existing_asset.publisher_id != user_id:
            raise PublishError(
                code=403,
                error="permission_denied",
                message="您无权限操作该插件",
            )
        by_name = asset_repo.list_by_publisher_name_and_type(user_id, name, "plugin")
        if len(by_name) == 1 and by_name[0].asset_id != pid:
            raise PublishError(
                code=422,
                error="plugin_id_mismatch",
                message=f"plugin_id 与插件包不匹配：您填写的 plugin_id='{pid}' 与插件名称 '{name}' 对应的插件id不一致",
                data={"expected_plugin_id": by_name[0].asset_id},
            )
        if len(by_name) > 1 and pid not in {m.asset_id for m in by_name}:
            raise PublishError(
                code=422,
                error="plugin_id_mismatch",
                message=f"plugin_id 与插件包不匹配：您填写的 plugin_id='{pid}' 与插件名称 '{name}' 对应的插件id不一致，请从同名候选中选择正确的 plugin_id",
                data={"ambiguous_plugin_ids": [m.asset_id for m in by_name]},
            )
        asset_id = pid
    else:
        matches = asset_repo.list_by_publisher_name_and_type(user_id, name, "plugin")
        if len(matches) > 1:
            raise PublishError(
                code=422,
                error="manifest_validation_failed",
                message=f"存在多个同名插件 '{name}'，请通过 plugin_id 指定要发布版本的插件",
                data={"ambiguous_plugin_ids": [m.asset_id for m in matches]},
            )
        if len(matches) == 1:
            # 同发布者 + 包内 name 唯一定位一条插件：不传 plugin_id 也可发新版 / 幂等重试
            existing_asset = matches[0]
            asset_id = existing_asset.asset_id
        else:
            asset_id = uuid.uuid4().hex
            existing_asset = None
    existing_version = version_repo.get_version(asset_id=asset_id, version=version)

    version_dir = _version_dir_prefix(user_id, asset_id, version, plugin_type)
    zip_key = _build_storage_path(
        publisher_id=user_id,
        asset_id=asset_id,
        version=version,
        asset_name=name,
        plugin_type=plugin_type,
    )
    file_path = version_dir

    if existing_version and _publish_idempotent_same_artifact(existing_version, computed):
        asset_for_result = existing_asset if existing_asset is not None else asset_repo.get_by_asset_id(asset_id)
        if asset_for_result is None:
            raise PublishError(
                code=500,
                error="internal_error",
                message="发布幂等校验失败：缺少插件主记录",
            )
        logger.info(
            "publish idempotent skip (same version + artifact_sha256): asset_id=%s version=%s",
            asset_id,
            version,
        )
        return _make_publish_result(asset_for_result, existing_version, zip_key)

    if existing_version and not force:
        raise PublishError(
            code=409,
            error="version_conflict",
            message=f"插件 '{name}' 版本 '{version}' 已存在，如需覆盖请设置 force=true",
            data={
                "existing_plugin": {
                    "plugin_id": existing_asset.asset_id if existing_asset else asset_id,
                    "version": existing_version.version,
                }
            },
        )

    # 写入校验和/大小到对象 metadata，避免下载时读全量对象重复计算
    upload_result = storage.upload_bytes(
        content,
        zip_key,
        metadata={"sha256": computed, "size": str(content_size)},
    )
    if not upload_result.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=upload_result.get("error", "插件包上传失败"),
        )

    if icon_bytes:
        icon_key = f"{version_dir}icon.png"
        r = storage.upload_bytes(icon_bytes, icon_key)
        if not r.get("success"):
            raise PublishError(
                code=500,
                error="storage_error",
                message=r.get("error", "插件图标上传失败"),
            )

    if detail_desc is not None:
        readme_key = f"{version_dir}readme.md"
        r = storage.upload_bytes(detail_desc.encode("utf-8"), readme_key)
        if not r.get("success"):
            raise PublishError(
                code=500,
                error="storage_error",
                message=r.get("error", "插件 README 上传失败"),
            )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Validate skill count for new skill assets (system admin is exempt).
    is_system_admin_publisher = (user_id or "").strip() == (settings.system_admin_user or "").strip()
    if not existing_asset and rt == "skill" and not is_system_admin_publisher:
        skill_count = asset_repo.count_skills_by_publisher(user_id)
        if skill_count >= 50:
            raise PublishError(
                code=409,
                error="skill_limit_exceeded",
                message=f"您已发布 {skill_count} 个 Skill，达到发布上限（最多 50 个）",
            )

    try:
        if not existing_asset:
            # 新建插件：插入主表 + 版本表（审核态在版本上，主表由聚合重算）
            mod_st, mod_rs = _moderation_for_publish(user_id=user_id, plugin_type=plugin_type)
            asset_obj = MarketAssetDB(
                asset_id=asset_id,
                asset_type="plugin",
                name=name,
                display_name=display_name,
                short_desc=short_desc,
                detail_desc=detail_desc,
                publisher_id=user_id,
                publisher_name=publisher_name,
                tags=tags if tags else None,
                status="PUBLISHED",
                plugin_type=plugin_type,
                latest_version=version,
                create_time=now_ms,
                update_time=now_ms,
            )
            version_obj = MarketAssetVersionDB(
                version_id=uuid.uuid4().hex,
                asset_id=asset_id,
                version=version,
                changelog=version_desc,
                status="ACTIVE",
                create_time=now_ms,
                file_path=file_path,
                artifact_sha256=computed,
                has_icon=bool(icon_bytes),
                moderation_status=mod_st,
                moderation_reject_reason=mod_rs if mod_st == MODERATION_REJECTED else None,
            )
            db.add(asset_obj)
            db.add(version_obj)
            _apply_skill_asset_aggregate_from_versions(db, asset_id)
            db.commit()
            db.refresh(asset_obj)
            db.refresh(version_obj)
            asset = asset_obj
            version_row = version_obj
        else:
            # 已有插件：更新主表 + 新增或覆盖版本（不直接写主表审核态）
            existing_asset.name = name
            existing_asset.display_name = display_name
            existing_asset.latest_version = version
            existing_asset.update_time = now_ms
            existing_asset.short_desc = short_desc
            existing_asset.detail_desc = detail_desc
            existing_asset.tags = tags if tags else None
            existing_asset.publisher_name = publisher_name
            existing_asset.plugin_type = plugin_type
            mod_st, mod_rs = _moderation_for_publish(user_id=user_id, plugin_type=plugin_type)

            if existing_version and force:
                existing_version.changelog = version_desc
                existing_version.status = "ACTIVE"
                existing_version.file_path = file_path
                existing_version.artifact_sha256 = computed
                existing_version.has_icon = bool(icon_bytes)
                existing_version.moderation_status = mod_st
                existing_version.moderation_reject_reason = mod_rs if mod_st == MODERATION_REJECTED else None
                version_row = existing_version
            else:
                version_row = MarketAssetVersionDB(
                    version_id=uuid.uuid4().hex,
                    asset_id=asset_id,
                    version=version,
                    changelog=version_desc,
                    status="ACTIVE",
                    create_time=now_ms,
                    file_path=file_path,
                    artifact_sha256=computed,
                    has_icon=bool(icon_bytes),
                    moderation_status=mod_st,
                    moderation_reject_reason=mod_rs if mod_st == MODERATION_REJECTED else None,
                )
                db.add(version_row)
            db.add(existing_asset)
            _apply_skill_asset_aggregate_from_versions(db, asset_id)
            db.commit()
            db.refresh(existing_asset)
            db.refresh(version_row)
            asset = existing_asset

    except IntegrityError as e:
        db.rollback()
        if _is_uk_publisher_name_error(e):
            raise PublishError(
                code=409,
                error="plugin_name_exists",
                message=f"您已发布过同名插件 '{name}'，请使用其他名称或为现有插件添加新版本",
            ) from e
        if _is_uk_asset_version_error(e):
            raise PublishError(
                code=409,
                error="version_exists",
                message=f"插件版本 '{version}' 已存在，如需覆盖请设置 force=true",
                data={"existing_version": version},
            ) from e
        raise

    if mod_st == MODERATION_PENDING and rt == "skill":
        try:
            notify_review_admins_new_skill_submission(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify review admins failed: %s", exc)

    # Cumulative changelog for this release dir
    all_versions = version_repo.list_versions_chronological(asset_id)
    changelog_text = _render_cumulative_changelog_file(all_versions)
    cl_key = f"{version_dir}changelog.log"
    r = storage.upload_bytes(changelog_text.encode("utf-8"), cl_key)
    if not r.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=r.get("error", "插件 changelog.log 上传失败"),
        )

    storage_url = zip_key
    published_at = datetime.fromtimestamp(
        (version_row.create_time or asset.create_time) / 1000, tz=timezone.utc
    ).isoformat()

    return PluginPublishResult(
        plugin_id=asset.asset_id,
        name=asset.name,
        version=version_row.version,
        status=version_row.status or "ACTIVE",
        published_at=published_at,
        storage_url=storage_url,
        plugin_type=asset.plugin_type,
    )


def _rows_pin_order_first(
    ordered: List[Tuple[MarketAssetDB, Optional[str], bool]],
) -> List[Tuple[MarketAssetDB, Optional[str], bool]]:
    """检索结果内：pin_order 非空的条目提前，按 pin_order 升序，同序保持原检索先后；其余保持检索顺序。"""
    pinned = [
        (row[0].pin_order, idx, row)
        for idx, row in enumerate(ordered)
        if row[0].pin_order is not None
    ]
    pinned.sort(key=lambda x: (x[0], x[1]))
    pinned_ids = {x[2][0].asset_id for x in pinned}
    unpinned = [row for row in ordered if row[0].asset_id not in pinned_ids]
    return [x[2] for x in pinned] + unpinned


def _icon_presigned_url_from_file_path(
    storage: S3StorageClient,
    file_path: str | None,
    has_icon: bool = False,
) -> str | None:
    """图标固定为版本目录下 icon.png，与 file_path 拼出对象 Key 后预签名。"""
    if not has_icon:
        return None
    prefix = _version_prefix_from_file_path(storage, file_path)
    if not prefix:
        return None
    icon_key = f"{prefix}icon.png"
    try:
        return storage.presigned_get_url(icon_key)
    except Exception as e:
        logger.warning("预签名图标链接失败 key=%s: %s", icon_key, e)
        return None


def _asset_matches_list_moderation_filter(asset: MarketAssetDB, ms: str) -> bool:
    raw = getattr(asset, "moderation_status", None)
    if ms == MODERATION_PENDING:
        return (raw or "").strip().upper() == MODERATION_PENDING
    if ms == MODERATION_REJECTED:
        return (raw or "").strip().upper() == MODERATION_REJECTED
    if ms == MODERATION_APPROVED:
        return moderation_coalesce_display(raw) == MODERATION_APPROVED
    return True


def _asset_matches_list_moderation_filter_retrieval(
    asset: MarketAssetDB,
    ms: str,
    *,
    pending_version_asset_ids: set[str],
) -> bool:
    """检索路径的 PENDING 筛选：Skill 含「任一字审中」与主表 PENDING 一致。"""
    if ms != MODERATION_PENDING:
        return _asset_matches_list_moderation_filter(asset, ms)
    if (asset.plugin_type or "").lower() != "skill":
        return _asset_matches_list_moderation_filter(asset, ms)
    raw = (getattr(asset, "moderation_status", None) or "").strip().upper()
    if raw == MODERATION_PENDING:
        return True
    return asset.asset_id in pending_version_asset_ids


def _filter_skill_version_strings_for_viewer(
    vrows: List[MarketAssetVersionDB],
    plugin_type: str | None,
    publisher_id: str,
    viewer: ViewerContext,
) -> List[str]:
    if (plugin_type or "").lower() != "skill":
        return [r.version for r in vrows]
    if viewer.is_market_moderation_admin:
        return [r.version for r in vrows]
    uid = (viewer.user_id or "").strip()
    if uid and uid == (publisher_id or "").strip():
        return [r.version for r in vrows]
    return [
        r.version
        for r in vrows
        if moderation_coalesce_display(getattr(r, "moderation_status", None)) == MODERATION_APPROVED
    ]


def _skill_version_moderation_map_for_list(
    asset: MarketAssetDB,
    vrows: List[MarketAssetVersionDB],
    viewer: ViewerContext,
) -> dict[str, str] | None:
    """发布者或审核员在列表/详情拉取时可拿到各版本审核状态，供前端版本下拉展示。"""
    if (asset.plugin_type or "").lower() != "skill":
        return None
    uid = (viewer.user_id or "").strip()
    pub = (asset.publisher_id or "").strip()
    if not (viewer.is_market_moderation_admin or (uid and uid == pub)):
        return None
    out: dict[str, str] = {}
    aid = (asset.asset_id or "").strip()
    for r in vrows:
        if (r.asset_id or "").strip() != aid:
            continue
        out[r.version] = moderation_coalesce_display(getattr(r, "moderation_status", None))
    return out or None


def _skill_has_pending_version_for_viewer(
    vrows: List[MarketAssetVersionDB],
    plugin_type: str | None,
    publisher_id: str,
    viewer: ViewerContext,
) -> bool:
    if (plugin_type or "").lower() != "skill":
        return False
    if not (
        viewer.is_market_moderation_admin
        or ((viewer.user_id or "").strip() == (publisher_id or "").strip() and (viewer.user_id or "").strip())
    ):
        return False
    return any(
        moderation_coalesce_display(getattr(r, "moderation_status", None)) == MODERATION_PENDING
        for r in vrows
    )


def _list_item_from_asset(
    asset: MarketAssetDB,
    latest_file_path: str | None,
    has_icon: bool,
    storage: S3StorageClient,
    vrows: List[MarketAssetVersionDB],
    viewer: ViewerContext,
) -> PluginListItem:
    item = PluginListItem.model_validate(asset)
    item.detail_desc = _detail_desc_for_display(asset.plugin_type, item.detail_desc)
    item.icon_uri = _icon_presigned_url_from_file_path(storage, latest_file_path, has_icon)
    item.public_latest_version = getattr(asset, "public_latest_version", None)
    item.all_versions = _filter_skill_version_strings_for_viewer(
        vrows, asset.plugin_type, asset.publisher_id, viewer
    )
    item.has_pending_skill_version = _skill_has_pending_version_for_viewer(
        vrows, asset.plugin_type, asset.publisher_id, viewer
    )
    item.skill_version_moderation = _skill_version_moderation_map_for_list(asset, vrows, viewer)
    return _list_item_with_viewer_flag(item, viewer)


def list_plugins_service(
    query: PluginListQuery,
    db: Session,
    storage: S3StorageClient,
    *,
    viewer: ViewerContext,
) -> PluginListResponse:
    logger.info(
        "List plugins request: page=%s page_size=%s asset_id=%s "
        "publisher_id=%s category_id=%s plugin_type=%s moderation_status=%s order_by=%s desc=%s",
        query.page,
        query.page_size,
        query.asset_id,
        query.publisher_id,
        query.category_id,
        query.plugin_type,
        query.moderation_status,
        query.order_by,
        query.desc,
    )
    repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    keyword = (query.search_keyword or "").strip()
    if not query.plugin_type and not query.plugin_type_exclude:
        query = query.model_copy(update={"plugin_type": "skill"})
    plugin_type = (query.plugin_type or "skill").strip()

    if keyword and plugin_type:
        item_ids = retrieval_search(get_index_manager(), plugin_type, keyword, query.page, query.page_size,
                                    method=settings.retrieval_search_method)
        if item_ids is not None:
            logger.info("retrieval path: plugin_type=%s keyword=%r hits=%d", plugin_type, keyword, len(item_ids))
            rows_with_path = repo.get_assets_with_file_paths(item_ids, viewer=viewer)
            rows_map = {asset.asset_id: (asset, fp, hi) for asset, fp, hi in rows_with_path}
            # preserve retrieval ranking; rows_map excludes OFFLINE (defensive filter)
            ordered = [rows_map[iid] for iid in item_ids if iid in rows_map]
            ms_list = (query.moderation_status or "").strip().upper() if query.moderation_status else ""
            if ms_list in (MODERATION_PENDING, MODERATION_APPROVED, MODERATION_REJECTED):
                ids_for_pending = [row[0].asset_id for row in ordered]
                pending_extra: set[str] = set()
                if ms_list == MODERATION_PENDING and (plugin_type or "").lower() == "skill":
                    pending_extra = version_repo.asset_ids_with_pending_moderation_version(ids_for_pending)
                ordered = [
                    row
                    for row in ordered
                    if _asset_matches_list_moderation_filter_retrieval(
                        row[0],
                        ms_list,
                        pending_version_asset_ids=pending_extra,
                    )
                ]
            if query.category_id and query.category_id.strip():
                category_id = query.category_id.strip()
                ordered = [row for row in ordered if (row[0].category_id or "") == category_id]
            ordered = _rows_pin_order_first(ordered)

            total = len(ordered)
            start = (query.page - 1) * query.page_size
            page_slice = ordered[start : start + query.page_size]
            page_asset_ids = [a.asset_id for a, _, _ in page_slice]
            vrows = version_repo.list_all_by_asset_ids(page_asset_ids)
            vmap: Dict[str, List[MarketAssetVersionDB]] = defaultdict(list)
            for r in vrows:
                vmap[r.asset_id].append(r)
            items = []
            for asset, latest_file_path, has_icon in page_slice:
                items.append(
                    _list_item_from_asset(
                        asset,
                        latest_file_path,
                        has_icon,
                        storage,
                        vmap.get(asset.asset_id, []),
                        viewer,
                    )
                )
            return PluginListResponse(
                page=query.page,
                page_size=query.page_size,
                total=total,
                items=items,
            )
        logger.info("retrieval unavailable for plugin_type=%s, fallback to DB LIKE", plugin_type)

    rows, total = repo.list_plugins(query, viewer=viewer)
    logger.info("List plugins query done: total=%s rows=%s", total, len(rows))
    asset_ids = [a.asset_id for a, _, _ in rows]
    vrows = version_repo.list_all_by_asset_ids(asset_ids)
    vmap: Dict[str, List[MarketAssetVersionDB]] = defaultdict(list)
    for r in vrows:
        vmap[r.asset_id].append(r)
    items = []
    for asset, latest_file_path, has_icon in rows:
        items.append(
            _list_item_from_asset(
                asset,
                latest_file_path,
                has_icon,
                storage,
                vmap.get(asset.asset_id, []),
                viewer,
            )
        )
    return PluginListResponse(
        page=query.page,
        page_size=query.page_size,
        total=total,
        items=items,
    )


def _skill_visible_to_marketplace_viewer(
    asset: MarketAssetDB,
    viewer: ViewerContext,
    db: Session,
) -> bool:
    """与 skill_moderation_list_clause 对齐：公网/非发布者是否可见 Skill（详情、下载）。"""
    if (asset.plugin_type or "").strip().lower() != "skill":
        return True
    if viewer.is_market_moderation_admin:
        return True
    uid = (viewer.user_id or "").strip()
    pub = (asset.publisher_id or "").strip()
    if uid and pub and uid == pub:
        return True
    raw = (getattr(asset, "moderation_status", None) or "").strip().upper()
    if raw == MODERATION_PENDING or raw == MODERATION_REJECTED:
        return False
    if raw == MODERATION_APPROVED:
        return True
    version_repo = MarketAssetVersionRepository(db)
    if version_repo.asset_has_explicit_pending_moderation_version(asset.asset_id):
        return version_repo.asset_has_explicit_approved_moderation_version(asset.asset_id)
    return True


def get_plugin_version_detail_service(
    asset_id: str,
    version: str,
    db: Session,
    storage: S3StorageClient,
    *,
    viewer: ViewerContext,
) -> PluginVersionDetail:
    logger.info("Get plugin version detail request: asset_id=%s version=%s", asset_id, version)
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        logger.warning("Get plugin version detail failed: asset not found, asset_id=%s", asset_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if not _skill_visible_to_marketplace_viewer(asset, viewer, db):
        logger.warning("Get plugin version detail forbidden: moderation, asset_id=%s", asset_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    version_row = version_repo.get_version(asset_id=asset_id, version=version)
    if not version_row:
        logger.warning(
            "Get plugin version detail failed: version not found, asset_id=%s version=%s",
            asset_id,
            version,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    if not viewer.can_see_skill_version_row(asset, version_row):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    view_count_value = int(asset.view_count or 0)
    try:
        updated_rows = asset_repo.increase_view_count_atomic(asset_id=asset.asset_id)
        if updated_rows == 1:
            db.commit()
            db.refresh(asset)
            view_count_value = int(asset.view_count or 0)
        elif updated_rows != 1:
            logger.warning(
                "increase_view_count unexpected row count=%s asset_id=%s",
                updated_rows,
                asset.asset_id,
            )
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning(
            "increase_view_count failed asset_id=%s: %s",
            asset.asset_id,
            exc,
            exc_info=True,
        )

    return PluginVersionDetail(
        asset_id=asset.asset_id,
        version=version_row.version,
        asset_type=asset.asset_type,
        plugin_type=asset.plugin_type,
        moderation_status=getattr(asset, "moderation_status", None),
        moderation_reject_reason=getattr(asset, "moderation_reject_reason", None),
        version_moderation_status=getattr(version_row, "moderation_status", None),
        version_moderation_reject_reason=getattr(version_row, "moderation_reject_reason", None),
        viewer_is_market_moderation_admin=viewer.is_market_moderation_admin,
        name=asset.name,
        display_name=asset.display_name,
        short_desc=asset.short_desc,
        detail_desc=_detail_desc_for_display(asset.plugin_type, asset.detail_desc),
        publisher_id=asset.publisher_id,
        publisher_name=asset.publisher_name,
        tags=asset.tags,
        category_id=asset.category_id,
        category_name=asset.category_name,
        certification=asset.certification,
        changelog=version_row.changelog,
        file_path=version_row.file_path,
        icon_uri=_icon_presigned_url_from_file_path(storage, version_row.file_path, version_row.has_icon),
        install_count=int(asset.install_count or 0),
        view_count=view_count_value,
        update_time=int(version_row.create_time)
        if version_row.create_time is not None
        else None,
    )


def _key_from_object_uri(storage: Any, uri_or_key: str | None) -> str | None:
    if not uri_or_key:
        return None
    raw = uri_or_key.strip()
    if not raw:
        return None
    if "://" not in raw:
        return raw
    try:
        p = urlparse(raw)
        path = (p.path or "").lstrip("/")
        bucket = getattr(getattr(storage, "config", None), "bucket_name", None)
        if bucket and path.startswith(f"{bucket}/"):
            return path[len(bucket) + 1:]
        return path
    except Exception:
        return None


def _version_prefix_from_file_path(storage: Any, file_path: str | None) -> str | None:
    prefix = _key_from_object_uri(storage, file_path)
    if not prefix:
        return None
    prefix = prefix.strip()
    return prefix if prefix.endswith("/") else prefix + "/"


def delete_plugin_version_service(
    asset_id: str,
    version: str,
    auth: AuthContext,
    db: Session,
    storage: S3StorageClient,
) -> PluginVersionDeleteData:
    logger.info("Delete plugin version request: asset_id=%s version=%s", asset_id, version)
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        logger.warning("Delete plugin version failed: asset not found, asset_id=%s", asset_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if not auth.is_admin and auth.acting_user_id and asset.publisher_id != auth.acting_user_id:
        logger.warning(
            "Delete plugin version forbidden: asset_id=%s acting_user_id=%s publisher_id=%s",
            asset_id,
            auth.acting_user_id,
            asset.publisher_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    saved_plugin_type = asset.plugin_type
    prefixes: list[str] = []

    if version.strip().lower() == "all":
        logger.info("Delete all versions for asset_id=%s", asset_id)
        versions = version_repo.list_versions(asset_id)
        if not versions:
            logger.warning("Delete all versions failed: no versions found, asset_id=%s", asset_id)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No versions found for asset")
        for v in versions:
            p = _version_prefix_from_file_path(storage, v.file_path)
            if p:
                prefixes.append(p)
        version_repo.delete_all_versions(asset_id)
        asset_repo.delete_asset(asset_id)
        logger.info("Delete all versions done: asset deleted, asset_id=%s", asset_id)
    else:
        logger.info("Delete single version: asset_id=%s version=%s", asset_id, version)
        version_row = version_repo.get_version(asset_id=asset_id, version=version)
        if not version_row:
            logger.warning(
                "Delete single version failed: version not found, asset_id=%s version=%s",
                asset_id,
                version,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
        p = _version_prefix_from_file_path(storage, version_row.file_path)
        if p:
            prefixes.append(p)
        version_repo.delete_version(asset_id, version)
        if version_repo.count_versions(asset_id) == 0:
            asset_repo.delete_asset(asset_id)
            logger.info("Delete single version done: no versions left, asset deleted, asset_id=%s", asset_id)
        else:
            remaining = version_repo.list_versions(asset_id)
            if remaining:
                new_latest = remaining[0].version
                fresh_asset = asset_repo.get_by_asset_id(asset_id)
                if fresh_asset:
                    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    asset_repo.update(
                        fresh_asset,
                        {"latest_version": new_latest, "update_time": now_ms},
                    )
                    _apply_skill_asset_aggregate_from_versions(db, asset_id)
                    db.commit()
                    logger.info(
                        "Delete single version done: latest_version updated, asset_id=%s latest_version=%s",
                        asset_id,
                        new_latest,
                    )

    for p in prefixes:
        dr = storage.delete_prefix(p)
        if not dr.get("success"):
            logger.error(
                "Delete storage prefix failed: asset_id=%s version=%s prefix=%s errors=%s",
                asset_id,
                version,
                p,
                dr.get("errors", []),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "Object storage delete failed",
                    "prefix": p,
                    "errors": dr.get("errors", []),
                },
            )
        logger.info("Delete storage prefix success: asset_id=%s prefix=%s", asset_id, p)

    logger.info("Delete plugin version success: asset_id=%s version=%s", asset_id, version)
    return PluginVersionDeleteData(asset_id=asset_id, version=version, plugin_type=saved_plugin_type)


def _build_artifact_key(
    publisher_id: str,
    asset_id: str,
    version: str,
    name: str,
    plugin_type: str | None = None,
) -> str:
    safe_name = name.strip().replace(" ", "-")
    root = _storage_root(plugin_type)
    return f"{root}/{publisher_id}/{asset_id}/{version}/{safe_name}_{version}.zip"


def _build_raw_artifact_key(
    publisher_id: str,
    asset_id: str,
    version: str,
    name: str,
    plugin_type: str | None = None,
) -> str:
    safe_name = name.strip().replace(" ", "-")
    root = _storage_root(plugin_type)
    return f"{root}/{publisher_id}/{asset_id}/{version}/{safe_name}_{version}.raw.zip"


def _extract_size_and_checksum_from_head(head: dict[str, Any]) -> tuple[int | None, str]:
    metadata = head.get("metadata") or {}
    checksum_sha256 = str(metadata.get("sha256") or "").strip().lower()
    size_meta = str(metadata.get("size") or "").strip()
    size: int | None = None
    if size_meta:
        try:
            size = int(size_meta)
        except ValueError:
            size = None
    if size is None:
        try:
            size = int(head.get("size")) if head.get("size") is not None else None
        except Exception:
            size = None
    return size, checksum_sha256


def _download_object_to_local_file(storage: S3StorageClient, key: str, target_file: str) -> None:
    body = None
    try:
        resp = storage.s3_client.get_object(Bucket=storage.config.bucket_name, Key=key)
        body = resp.get("Body")
        if body is None:
            raise PublishError(code=500, error="storage_error", message=f"读取对象 body 为空: key={key}")
        with open(target_file, "wb") as wf:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                wf.write(chunk)
    except PublishError:
        raise
    except Exception as e:
        raise PublishError(code=500, error="storage_error", message=f"下载对象失败: {e}") from e
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass


def _compute_file_sha256_and_size(path: str) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total = 0
    with open(path, "rb") as rf:
        while True:
            chunk = rf.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            total += len(chunk)
    return hasher.hexdigest(), total


def _resolve_package_root_by_first_skill_md(extract_dir: str) -> str:
    """在解压目录中找到第一个 SKILL.md，并将其所在目录作为 package_root。"""
    first_skill_md: str | None = None
    for cur_root, dirs, files in os.walk(extract_dir):
        dirs.sort()
        files.sort()
        for filename in files:
            if filename.lower() == "skill.md":
                first_skill_md = os.path.join(cur_root, filename)
                break
        if first_skill_md:
            break

    if not first_skill_md:
        raise PublishError(
            code=500,
            error="raw_zip_build_failed",
            message="原始插件包结构不合法：缺少 SKILL.md",
        )
    package_root = os.path.dirname(first_skill_md)
    if not os.path.basename(package_root).strip():
        raise PublishError(
            code=500,
            error="raw_zip_build_failed",
            message="原始插件包结构不合法：无法从 SKILL.md 推导技能目录名",
        )
    return package_root


def _build_raw_zip_from_original(
    *,
    source_zip: str,
    output_zip: str,
    skill_name: str,
    version: str,
) -> None:
    """从已发布 zip 生成 raw.zip：仅打包首个 SKILL.md 所在目录本身（不追加额外前缀）。"""
    _ = (skill_name, version)

    with tempfile.TemporaryDirectory(prefix="market_raw_zip_extract_") as extract_dir:
        with zipfile.ZipFile(source_zip, "r") as zf:
            zf.extractall(extract_dir)

        package_root = _resolve_package_root_by_first_skill_md(extract_dir)
        parent_dir = os.path.dirname(package_root)
        archive_base = os.path.splitext(output_zip)[0]
        built_zip = shutil.make_archive(
            base_name=archive_base,
            format="zip",
            root_dir=package_root,
            base_dir=".",
        )
        if os.path.normpath(built_zip) != os.path.normpath(output_zip):
            shutil.move(built_zip, output_zip)


def _ensure_non_cli_raw_artifact(
    *,
    storage: S3StorageClient,
    old_key: str,
    raw_key: str,
    skill_name: str,
    version: str,
) -> tuple[str, int, str]:
    raw_head = storage.head_object(raw_key)
    if raw_head.get("success"):
        raw_size, raw_checksum = _extract_size_and_checksum_from_head(raw_head)
        if raw_size is not None and raw_checksum:
            return raw_key, int(raw_size), raw_checksum

    try:
        with tempfile.TemporaryDirectory(prefix="market_raw_zip_build_") as tmp_dir:
            old_zip_file = os.path.join(tmp_dir, "origin.zip")
            raw_zip_file = os.path.join(tmp_dir, "origin.raw.zip")
            _download_object_to_local_file(storage, old_key, old_zip_file)
            _build_raw_zip_from_original(
                source_zip=old_zip_file,
                output_zip=raw_zip_file,
                skill_name=skill_name,
                version=version,
            )
            checksum, size = _compute_file_sha256_and_size(raw_zip_file)
            with open(raw_zip_file, "rb") as rf:
                storage.s3_client.put_object(
                    Bucket=storage.config.bucket_name,
                    Key=raw_key,
                    Body=rf,
                    Metadata={"sha256": checksum, "size": str(size)},
                )
            return raw_key, int(size), checksum
    except PublishError:
        raise
    except Exception as e:
        raise PublishError(code=500, error="raw_zip_build_failed", message=f"生成或上传 raw.zip 失败: {e}") from e


def _resolve_latest_version_for_download(
    *,
    asset_id: str,
    latest_version: str | None,
    version_repo: MarketAssetVersionRepository,
):
    if latest_version:
        row = version_repo.get_version(asset_id=asset_id, version=latest_version)
        if row:
            return row
    return version_repo.get_latest_version(asset_id=asset_id)


def moderate_skill_asset_service(
    *,
    asset_id: str,
    action: str,
    reason: str | None,
    version: str | None,
    auth: AuthContext,
    db: Session,
) -> SkillModerationResult:
    if not auth.is_market_moderation_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if (asset.plugin_type or "").lower() != "skill":
        raise PublishError(
            code=400,
            error="not_skill",
            message="仅支持对 Skill 类型资源进行审核",
        )
    vstr = (version or "").strip() or None
    if not vstr:
        vstr = (asset.latest_version or "").strip()
    if not vstr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    vrow = version_repo.get_version(asset_id=asset_id, version=vstr)
    if not vrow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    cur = moderation_coalesce_display(getattr(vrow, "moderation_status", None))
    act = (action or "").strip().lower()
    if act == "approve":
        if cur == MODERATION_APPROVED:
            db.refresh(asset)
            return SkillModerationResult(
                asset_id=asset.asset_id,
                moderation_status=(asset.moderation_status or MODERATION_APPROVED).strip(),
                moderation_reject_reason=asset.moderation_reject_reason,
                version=vstr,
            )
        if cur not in (MODERATION_PENDING, MODERATION_REJECTED):
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="当前版本审核状态不允许执行通过操作",
            )
        vrow.moderation_status = MODERATION_APPROVED
        vrow.moderation_reject_reason = None
    elif act == "reject":
        if cur == MODERATION_APPROVED:
            raise PublishError(
                code=409,
                error="moderation_version_locked",
                message="该版本已审核通过，不可驳回。",
            )
        if cur == MODERATION_REJECTED:
            raise PublishError(
                code=409,
                error="already_rejected",
                message="该版本已被驳回，请勿重复驳回；可先「审核通过」或等待发布者更新版本。",
            )
        r = (reason or "").strip()
        if not r:
            raise PublishError(
                code=422,
                error="reason_required",
                message="审核不通过时必须填写原因",
            )
        vrow.moderation_status = MODERATION_REJECTED
        vrow.moderation_reject_reason = r
    else:
        raise PublishError(
            code=400,
            error="invalid_action",
            message="action 必须为 approve 或 reject",
        )
    db.add(vrow)
    _apply_skill_asset_aggregate_from_versions(db, asset_id)
    publisher_id_for_notify = (asset.publisher_id or "").strip()
    db.commit()
    db.refresh(asset)
    dn = (getattr(asset, "display_name", None) or "").strip() or (getattr(asset, "name", None) or "").strip()
    sn = (getattr(asset, "name", None) or "").strip() or asset_id
    rr_audit: str | None = None
    if act == "reject":
        rr_audit = (getattr(vrow, "moderation_reject_reason", None) or "").strip() or None
    act_upper = "APPROVE" if act == "approve" else "REJECT"
    if act == "approve":
        detail_cn = f"审核通过 Skill「{dn}」({sn}) v{vstr}"
    else:
        detail_cn = f"驳回 Skill「{dn}」({sn}) v{vstr}，原因：{rr_audit or '—'}"
    audit_log(
        db=db,
        event_type=EVENT_SKILL_MODERATION,
        action=act_upper,
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type="skill",
        resource_id=asset_id,
        resource_version=vstr,
        result="SUCCESS",
        detail=detail_cn,
        ip_address=auth.ip_address,
        user_agent=auth.user_agent,
        extra={
            "skill_name": sn,
            "skill_display_name": (getattr(asset, "display_name", None) or "").strip() or None,
            "reject_reason": rr_audit,
        },
    )
    if act in ("approve", "reject") and publisher_id_for_notify:
        try:
            notify_publisher_skill_review_finished(db, publisher_id=publisher_id_for_notify)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify publisher review finished failed: %s", exc)
    return SkillModerationResult(
        asset_id=asset.asset_id,
        moderation_status=(asset.moderation_status or MODERATION_APPROVED).strip(),
        moderation_reject_reason=asset.moderation_reject_reason,
        version=vstr,
    )


def _audit_created_at_ms(created_at) -> int:
    if not created_at:
        return 0
    ca = created_at
    if ca.tzinfo is None:
        ca = ca.replace(tzinfo=_BJ_TZ)
    return int(ca.timestamp() * 1000)


def _extra_field_str(value: Any) -> str:
    """Normalize audit `extra` JSON values to a stripped string (handles non-string legacy data)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _extra_field_optional_str(value: Any) -> Optional[str]:
    s = _extra_field_str(value)
    return s or None


def list_my_skill_moderation_audits_service(
    *,
    auth: AuthContext,
    db: Session,
    page: int,
    page_size: int,
) -> SkillModerationAuditListResponse:
    if not auth.is_market_moderation_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    safe_page = max(1, page)
    safe_size = min(max(1, page_size), 100)
    rows, total = list_skill_moderation_audit_logs_for_operator(
        db,
        operator_id=auth.acting_user_id,
        page=safe_page,
        page_size=safe_size,
    )
    items: List[SkillModerationAuditListItem] = []
    for log in rows:
        extra_raw = log.extra
        extra: Dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
        sn = _extra_field_str(extra.get("skill_name")) or _extra_field_str(log.resource_id)
        sd = _extra_field_optional_str(extra.get("skill_display_name"))
        rr = _extra_field_optional_str(extra.get("reject_reason"))
        maj: Literal["APPROVE", "REJECT"] = (
            "REJECT" if (log.action or "").strip().upper() == "REJECT" else "APPROVE"
        )
        if maj == "APPROVE":
            rr = None
        ver = _extra_field_str(log.resource_version)
        items.append(
            SkillModerationAuditListItem(
                event_id=log.event_id,
                asset_id=_extra_field_str(log.resource_id),
                skill_name=sn or _extra_field_str(log.resource_id),
                skill_display_name=sd,
                version=ver or "—",
                moderation_action=maj,
                reject_reason=rr,
                created_at_ms=_audit_created_at_ms(log.created_at),
            )
        )
    return SkillModerationAuditListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=items,
    )


def get_download_info(
    *,
    asset_id: str,
    version: str | None = None,
    db: Session,
    storage: S3StorageClient,
    fetch_user_id: str | None = None,
    viewer: ViewerContext,
    is_cli_download: bool = False,
) -> PluginDownloadData:
    """根据 asset_id（可选 version）返回预签名下载信息。"""
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    fetch_repo = PluginFetchRecordRepository(db)

    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        raise PublishError(
            code=404,
            error="plugin_not_found",
            message=f"插件 '{asset_id}' 不存在",
        )
    if not _skill_visible_to_marketplace_viewer(asset, viewer, db):
        raise PublishError(
            code=404,
            error="plugin_not_found",
            message=f"插件 '{asset_id}' 不存在或暂不可下载",
        )

    version = (version or "").strip() or None
    if version is not None:
        if not VERSION_PATTERN.match(version):
            raise PublishError(
                code=422,
                error="invalid_version",
                data={"version": version},
                message="version 参数格式错误，应为 x.y.z（如 1.0.0）",
            )
        version_row = version_repo.get_version(asset_id=asset.asset_id, version=version)
        if not version_row:
            raise PublishError(
                code=404,
                error="version_not_found",
                data={"asset_id": asset.asset_id, "version": version},
                message=f"插件 '{asset.name}' 不存在版本 '{version}'",
            )
        if not viewer.can_see_skill_version_row(asset, version_row):
            raise PublishError(
                code=404,
                error="plugin_not_found",
                message=f"插件 '{asset.asset_id}' 不存在或暂不可下载",
            )
    else:
        pt = (asset.plugin_type or "").strip().lower()
        uid = (viewer.user_id or "").strip()
        pub = (asset.publisher_id or "").strip()
        is_owner = bool(uid and pub and uid == pub)
        if pt == "skill" and not is_owner and not viewer.is_market_moderation_admin:
            plv = (getattr(asset, "public_latest_version", None) or "").strip() or None
            if not plv:
                raise PublishError(
                    code=404,
                    error="plugin_not_found",
                    message=f"插件 '{asset.asset_id}' 不存在或暂不可下载",
                )
            version_row = version_repo.get_version(asset_id=asset.asset_id, version=plv)
            if not version_row or not viewer.can_see_skill_version_row(asset, version_row):
                raise PublishError(
                    code=404,
                    error="plugin_not_found",
                    message=f"插件 '{asset.asset_id}' 不存在或暂不可下载",
                )
        else:
            version_row = _resolve_latest_version_for_download(
                asset_id=asset.asset_id,
                latest_version=asset.latest_version,
                version_repo=version_repo,
            )
    if not version_row:
        raise PublishError(
            code=404,
            error="plugin_not_found",
            message=f"插件 '{asset.asset_id}' 暂无可下载版本",
        )

    normal_key = _build_artifact_key(
        publisher_id=asset.publisher_id,
        asset_id=asset.asset_id,
        version=version_row.version,
        name=asset.name,
        plugin_type=asset.plugin_type,
    )
    key = normal_key
    size: int | None = None
    checksum_sha256 = ""

    plugin_type_norm = (asset.plugin_type or "").strip().lower()
    if not is_cli_download and plugin_type_norm == RUNTIME_SKILL:
        raw_key = _build_raw_artifact_key(
            publisher_id=asset.publisher_id,
            asset_id=asset.asset_id,
            version=version_row.version,
            name=asset.name,
            plugin_type=asset.plugin_type,
        )
        key, size, checksum_sha256 = _ensure_non_cli_raw_artifact(
            storage=storage,
            old_key=normal_key,
            raw_key=raw_key,
            skill_name=asset.name,
            version=version_row.version,
        )

    head = storage.head_object(key)
    if not head.get("success"):
        if head.get("not_found"):
            raise PublishError(
                code=404,
                error="version_deleted",
                message="插件版本已被删除",
            )
        raise PublishError(
            code=500,
            error="storage_error",
            message=f"读取插件包元数据失败: {head.get('error', 'unknown')}",
        )

    download_filename = f"{asset.name}_{version_row.version}.zip"
    download_url = storage.presigned_get_url(
        key, download_filename=download_filename
    )


    if size is None or not checksum_sha256:
        size, checksum_sha256 = _extract_size_and_checksum_from_head(head)

    if size is None or not checksum_sha256:
        raise PublishError(
            code=500,
            error="storage_error",
            message="插件包对象缺少必要的元数据（sha256/size），请重新发布该版本",
        )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        updated_rows = asset_repo.increase_install_count_atomic(
            asset_id=asset.asset_id,
            now_ms=now_ms,
        )
        if updated_rows != 1:
            raise PublishError(
                code=500,
                error="db_error",
                message=f"更新下载统计失败：asset_id={asset.asset_id}",
            )

        fetch_repo.create_fetch_record(
            asset_id=asset.asset_id,
            version_id=version_row.version_id,
            fetch_user_id=fetch_user_id,
            create_time=now_ms,
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise PublishError(
            code=500,
            error="db_error",
            message="更新下载统计失败",
        ) from e

    return PluginDownloadData(
        download_url=download_url,
        asset_id=asset.asset_id,
        name=asset.name,
        version=version_row.version,
        file_size=int(size),
        checksum_sha256=checksum_sha256,
    )
