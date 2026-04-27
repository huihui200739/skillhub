from __future__ import annotations

from datetime import datetime, timezone
import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.errors import PublishError
from plugins_market.core.s3_storage_client import S3StorageClient
from plugins_market.models.market_assets import MarketAssetDB, SkillPatchDB
from plugins_market.repositories import (
    MarketAssetRepository,
    MarketAssetVersionRepository,
    SkillPatchRepository,
)
from plugins_market.schemas.skill_patch import (
    SkillPatchDeleteData,
    SkillPatchDetail,
    SkillPatchDownloadData,
    SkillPatchItem,
    SkillPatchListResponse,
    SkillPatchPublishResult,
)
from plugins_market.services.plugin import (
    _compute_checksum,
    _icon_presigned_url_from_file_path,
    _validate_version,
    _version_prefix_from_file_path,
)
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.constants import MAX_FILE_SIZE, RUNTIME_SKILL, VERSION_PATTERN

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _normalize_patch_version(raw: str | None, manifest_version: str | None) -> str:
    version = (raw or manifest_version or "").strip()
    if not version:
        raise PublishError(
            code=400,
            error="patch_version_required",
            message="自演进版本号必填，或上传包 plugin.yaml 中必须包含 version",
        )
    _validate_version(version)
    return version


def _normalize_patch_type(raw: str | None) -> str:
    value = (raw or "self-evolution").strip().lower()
    if not value:
        return "self-evolution"
    if len(value) > 32 or not all(c.isalnum() or c == "-" for c in value):
        raise PublishError(
            code=422,
            error="invalid_patch_type",
            message="patch_type 仅支持字母、数字和连字符，且长度不超过 32",
        )
    return value


def _patch_dir_prefix(publisher_id: str, skill_asset_id: str, patch_version: str) -> str:
    return f"skill-patches/{publisher_id}/{skill_asset_id}/{patch_version}/"


def _patch_zip_key(
    *,
    publisher_id: str,
    skill_asset_id: str,
    patch_version: str,
    skill_name: str,
) -> str:
    safe_name = skill_name.strip().replace(" ", "-")
    return f"{_patch_dir_prefix(publisher_id, skill_asset_id, patch_version)}{safe_name}_{patch_version}.zip"


def _patch_zip_key_from_row(
    *,
    storage: S3StorageClient,
    skill: MarketAssetDB,
    patch: SkillPatchDB,
) -> str:
    prefix = _version_prefix_from_file_path(storage, patch.file_path)
    if not prefix:
        raise PublishError(
            code=500,
            error="patch_storage_path_missing",
            message="自演进版本缺少对象存储路径，请重新发布该版本",
        )
    safe_name = skill.name.strip().replace(" ", "-")
    return f"{prefix}{safe_name}_{patch.patch_version}.zip"


def _ensure_skill_asset(
    *,
    repo: MarketAssetRepository,
    skill_asset_id: str,
) -> MarketAssetDB:
    asset = repo.get_by_asset_id(skill_asset_id)
    if not asset:
        raise PublishError(
            code=404,
            error="skill_not_found",
            message=f"Skill '{skill_asset_id}' 不存在",
        )
    if (asset.plugin_type or "").strip().lower() != RUNTIME_SKILL:
        raise PublishError(
            code=422,
            error="target_not_skill",
            message=f"资产 '{skill_asset_id}' 不是 Skill，不能创建自演进版本",
        )
    return asset


def _ensure_write_permission(
    *,
    skill: MarketAssetDB,
    acting_user_id: str,
    is_admin: bool,
) -> None:
    if is_admin:
        return
    if not acting_user_id or skill.publisher_id != acting_user_id:
        raise PublishError(
            code=403,
            error="permission_denied",
            message="您无权限操作该 Skill 的自演进版本",
        )


def _source_version_or_default(
    *,
    skill: MarketAssetDB,
    requested: str | None,
    version_repo: MarketAssetVersionRepository,
) -> str | None:
    source_version = (requested or skill.latest_version or "").strip() or None
    if source_version is None:
        return None
    if not VERSION_PATTERN.match(source_version):
        raise PublishError(
            code=422,
            error="invalid_source_skill_version",
            message="source_skill_version 格式错误，应为 x.y.z",
            data={"source_skill_version": source_version},
        )
    if not version_repo.get_version(asset_id=skill.asset_id, version=source_version):
        raise PublishError(
            code=404,
            error="source_skill_version_not_found",
            message=f"Skill '{skill.name}' 不存在正式版本 '{source_version}'",
        )
    return source_version


def _is_same_active_artifact(patch: SkillPatchDB | None, checksum: str) -> bool:
    if patch is None:
        return False
    if (patch.status or "").upper() != "ACTIVE":
        return False
    stored = (patch.artifact_sha256 or "").strip().lower()
    return bool(stored and stored == checksum.lower())


def _should_skip_patch_publish(
    *,
    existing_patch: SkillPatchDB | None,
    checksum: str,
    force: bool,
    require_existing: bool,
) -> bool:
    if force or require_existing:
        return False
    return _is_same_active_artifact(existing_patch, checksum)


def _is_skill_patch_version_integrity_error(msg: str) -> bool:
    if "uk_skill_patch_version" in msg:
        return True
    has_unique = "unique" in msg
    has_skill_asset_id = "skill_asset_id" in msg
    has_patch_version = "patch_version" in msg
    return has_unique and has_skill_asset_id and has_patch_version


def _make_publish_result(patch: SkillPatchDB, storage_url: str) -> SkillPatchPublishResult:
    ts_ms = patch.create_time or patch.update_time or 0
    published_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    return SkillPatchPublishResult(
        patch_id=patch.patch_id,
        skill_asset_id=patch.skill_asset_id,
        patch_version=patch.patch_version,
        source_skill_version=patch.source_skill_version,
        patch_type=patch.patch_type,
        status=patch.status or "ACTIVE",
        published_at=published_at,
        storage_url=storage_url,
    )


def _make_patch_item(storage: S3StorageClient, patch: SkillPatchDB) -> SkillPatchItem:
    return SkillPatchItem(
        patch_id=patch.patch_id,
        skill_asset_id=patch.skill_asset_id,
        source_skill_version=patch.source_skill_version,
        patch_version=patch.patch_version,
        patch_type=patch.patch_type,
        publisher_id=patch.publisher_id,
        publisher_name=patch.publisher_name,
        changelog=patch.changelog,
        status=patch.status,
        file_path=patch.file_path,
        artifact_sha256=patch.artifact_sha256,
        metadata=patch.patch_metadata,
        icon_uri=_icon_presigned_url_from_file_path(storage, patch.file_path),
        create_time=patch.create_time,
        update_time=patch.update_time,
    )


def _make_patch_detail(
    *,
    storage: S3StorageClient,
    skill: MarketAssetDB,
    patch: SkillPatchDB,
) -> SkillPatchDetail:
    return SkillPatchDetail(
        patch_id=patch.patch_id,
        skill_asset_id=patch.skill_asset_id,
        skill_name=skill.name,
        skill_display_name=skill.display_name,
        source_skill_version=patch.source_skill_version,
        patch_version=patch.patch_version,
        patch_type=patch.patch_type,
        publisher_id=patch.publisher_id,
        publisher_name=patch.publisher_name,
        changelog=patch.changelog,
        status=patch.status,
        file_path=patch.file_path,
        artifact_sha256=patch.artifact_sha256,
        metadata=patch.patch_metadata,
        icon_uri=_icon_presigned_url_from_file_path(storage, patch.file_path),
        create_time=patch.create_time,
        update_time=patch.update_time,
    )


def publish_skill_patch(
    *,
    skill_asset_id: str,
    acting_user_id: str,
    is_admin: bool,
    content: bytes,
    filename: str | None,
    expected_checksum: str,
    patch_version: str | None,
    source_skill_version: str | None,
    version_desc: str | None,
    patch_type: str | None,
    metadata: dict[str, Any] | None,
    force: bool,
    db: Session,
    storage: S3StorageClient,
    publisher_name_override: str | None = None,
    require_existing: bool = False,
) -> SkillPatchPublishResult:
    if not filename or not filename.lower().endswith(".zip"):
        raise PublishError(code=400, error="invalid_file_format", message="仅支持 .zip 格式的 Skill 自演进包")
    if len(content) > MAX_FILE_SIZE:
        raise PublishError(code=413, error="file_too_large", message="文件大小超过限制（最大512MB）")

    computed = _compute_checksum(content)
    if computed != expected_checksum.lower():
        raise PublishError(
            code=400,
            error="checksum_mismatch",
            message="文件校验和不匹配，文件可能在传输过程中损坏",
        )
    if len(content) < 2 or content[:2] != b"PK":
        raise PublishError(code=400, error="invalid_file_format", message="仅支持 .zip 格式的 Skill 自演进包")

    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    patch_repo = SkillPatchRepository(db)

    skill = _ensure_skill_asset(repo=asset_repo, skill_asset_id=skill_asset_id)
    _ensure_write_permission(skill=skill, acting_user_id=acting_user_id, is_admin=is_admin)

    meta = extract_plugin_metadata(content)
    name = (meta.get("name") or "").strip()
    runtime_type = (meta.get("plugin_type") or "").strip().lower()
    manifest_version = (meta.get("version") or "").strip()
    if runtime_type != RUNTIME_SKILL:
        raise PublishError(
            code=422,
            error="patch_package_not_skill",
            message="自演进版本包必须是 runtime.type=skill 的 Skill 包",
        )
    if name != skill.name:
        raise PublishError(
            code=422,
            error="patch_skill_name_mismatch",
            message=f"自演进版本包 name='{name}' 与目标 Skill name='{skill.name}' 不一致",
            data={"expected_name": skill.name, "actual_name": name},
        )

    resolved_patch_version = _normalize_patch_version(patch_version, manifest_version)
    resolved_patch_type = _normalize_patch_type(patch_type)
    resolved_source_version = _source_version_or_default(
        skill=skill,
        requested=source_skill_version,
        version_repo=version_repo,
    )

    existing_patch = patch_repo.get_patch(skill_asset_id=skill.asset_id, patch_version=resolved_patch_version)
    if require_existing and existing_patch is None:
        raise PublishError(
            code=404,
            error="skill_patch_not_found",
            message=f"Skill '{skill.name}' 不存在自演进版本 '{resolved_patch_version}'",
        )

    zip_key = _patch_zip_key(
        publisher_id=skill.publisher_id,
        skill_asset_id=skill.asset_id,
        patch_version=resolved_patch_version,
        skill_name=skill.name,
    )
    version_dir = _patch_dir_prefix(skill.publisher_id, skill.asset_id, resolved_patch_version)

    if _should_skip_patch_publish(
        existing_patch=existing_patch,
        checksum=computed,
        force=force,
        require_existing=require_existing,
    ):
        logger.info(
            "skill patch publish idempotent skip: skill_asset_id=%s patch_version=%s",
            skill.asset_id,
            resolved_patch_version,
        )
        return _make_publish_result(existing_patch, zip_key)

    if existing_patch and not force and not require_existing:
        raise PublishError(
            code=409,
            error="skill_patch_version_conflict",
            message=f"Skill '{skill.name}' 自演进版本 '{resolved_patch_version}' 已存在，如需覆盖请设置 force=true",
            data={
                "existing_patch": {
                    "skill_asset_id": skill.asset_id,
                    "patch_version": existing_patch.patch_version,
                }
            },
        )

    upload_result = storage.upload_bytes(
        content,
        zip_key,
        metadata={"sha256": computed, "size": str(len(content))},
    )
    if not upload_result.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=upload_result.get("error", "Skill 自演进包上传失败"),
        )

    icon_bytes = meta.get("icon_bytes") or b""
    icon_key = f"{version_dir}icon.png"
    r = storage.upload_bytes(icon_bytes, icon_key)
    if not r.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=r.get("error", "Skill 自演进图标上传失败"),
        )

    detail_desc = meta.get("detail_desc")
    if detail_desc is not None:
        readme_key = f"{version_dir}readme.md"
        r = storage.upload_bytes(str(detail_desc).encode("utf-8"), readme_key)
        if not r.get("success"):
            raise PublishError(
                code=500,
                error="storage_error",
                message=r.get("error", "Skill 自演进 README 上传失败"),
            )

    changelog_text = (version_desc or "（无变更说明）").strip() + "\n"
    r = storage.upload_bytes(changelog_text.encode("utf-8"), f"{version_dir}changelog.log")
    if not r.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=r.get("error", "Skill 自演进 changelog.log 上传失败"),
        )

    now_ms = _now_ms()
    publisher_name = (
        publisher_name_override.strip()
        if publisher_name_override and publisher_name_override.strip()
        else skill.publisher_name
    )

    try:
        if existing_patch:
            existing_patch.source_skill_version = resolved_source_version
            existing_patch.patch_type = resolved_patch_type
            existing_patch.publisher_id = skill.publisher_id
            existing_patch.publisher_name = publisher_name
            existing_patch.changelog = version_desc
            existing_patch.status = "ACTIVE"
            existing_patch.file_path = version_dir
            existing_patch.artifact_sha256 = computed
            existing_patch.patch_metadata = metadata
            existing_patch.update_time = now_ms
            db.add(existing_patch)
            db.commit()
            db.refresh(existing_patch)
            patch = existing_patch
        else:
            patch = SkillPatchDB(
                patch_id=uuid.uuid4().hex,
                skill_asset_id=skill.asset_id,
                source_skill_version=resolved_source_version,
                patch_version=resolved_patch_version,
                patch_type=resolved_patch_type,
                publisher_id=skill.publisher_id,
                publisher_name=publisher_name,
                changelog=version_desc,
                status="ACTIVE",
                file_path=version_dir,
                artifact_sha256=computed,
                patch_metadata=metadata,
                create_time=now_ms,
                update_time=now_ms,
            )
            db.add(patch)
            db.commit()
            db.refresh(patch)
    except IntegrityError as e:
        db.rollback()
        msg = str(getattr(e, "orig", None) or e).lower()
        if _is_skill_patch_version_integrity_error(msg):
            raise PublishError(
                code=409,
                error="skill_patch_version_exists",
                message=f"Skill 自演进版本 '{resolved_patch_version}' 已存在，如需覆盖请设置 force=true",
                data={"existing_patch_version": resolved_patch_version},
            ) from e
        raise
    except SQLAlchemyError as e:
        db.rollback()
        raise PublishError(
            code=500,
            error="db_error",
            message="写入 Skill 自演进版本失败",
        ) from e

    return _make_publish_result(patch, zip_key)


def list_skill_patches_service(
    *,
    skill_asset_id: str,
    page: int,
    page_size: int,
    status: str | None,
    db: Session,
    storage: S3StorageClient,
) -> SkillPatchListResponse:
    asset_repo = MarketAssetRepository(db)
    patch_repo = SkillPatchRepository(db)
    _ensure_skill_asset(repo=asset_repo, skill_asset_id=skill_asset_id)
    rows, total = patch_repo.list_patches(
        skill_asset_id=skill_asset_id,
        page=page,
        page_size=page_size,
        status=status,
    )
    return SkillPatchListResponse(
        page=max(1, page),
        page_size=max(1, min(page_size, 100)),
        total=total,
        items=[_make_patch_item(storage, row) for row in rows],
    )


def get_skill_patch_detail_service(
    *,
    skill_asset_id: str,
    patch_version: str,
    db: Session,
    storage: S3StorageClient,
) -> SkillPatchDetail:
    asset_repo = MarketAssetRepository(db)
    patch_repo = SkillPatchRepository(db)
    skill = _ensure_skill_asset(repo=asset_repo, skill_asset_id=skill_asset_id)
    patch = patch_repo.get_patch(skill_asset_id=skill.asset_id, patch_version=patch_version)
    if not patch:
        raise PublishError(
            code=404,
            error="skill_patch_not_found",
            message=f"Skill '{skill.name}' 不存在自演进版本 '{patch_version}'",
        )
    return _make_patch_detail(storage=storage, skill=skill, patch=patch)


def delete_skill_patch_service(
    *,
    skill_asset_id: str,
    patch_version: str,
    acting_user_id: str,
    is_admin: bool,
    db: Session,
    storage: S3StorageClient,
) -> SkillPatchDeleteData:
    asset_repo = MarketAssetRepository(db)
    patch_repo = SkillPatchRepository(db)
    skill = _ensure_skill_asset(repo=asset_repo, skill_asset_id=skill_asset_id)
    _ensure_write_permission(skill=skill, acting_user_id=acting_user_id, is_admin=is_admin)

    prefixes: list[str] = []
    normalized = patch_version.strip()
    if normalized.lower() == "all":
        rows = patch_repo.list_all_patches(skill.asset_id)
        if not rows:
            raise PublishError(
                code=404,
                error="skill_patch_not_found",
                message=f"Skill '{skill.name}' 暂无自演进版本",
            )
        for row in rows:
            prefix = _version_prefix_from_file_path(storage, row.file_path)
            if prefix:
                prefixes.append(prefix)
        patch_repo.delete_all_patches(skill.asset_id)
    else:
        patch = patch_repo.get_patch(skill_asset_id=skill.asset_id, patch_version=normalized)
        if not patch:
            raise PublishError(
                code=404,
                error="skill_patch_not_found",
                message=f"Skill '{skill.name}' 不存在自演进版本 '{normalized}'",
            )
        prefix = _version_prefix_from_file_path(storage, patch.file_path)
        if prefix:
            prefixes.append(prefix)
        patch_repo.delete_patch(skill.asset_id, normalized)

    for prefix in prefixes:
        dr = storage.delete_prefix(prefix)
        if not dr.get("success"):
            raise PublishError(
                code=502,
                error="storage_delete_failed",
                message="对象存储删除失败",
                data={"prefix": prefix, "errors": dr.get("errors", [])},
            )

    return SkillPatchDeleteData(skill_asset_id=skill.asset_id, patch_version=normalized)


def get_skill_patch_download_info(
    *,
    skill_asset_id: str,
    patch_version: str,
    db: Session,
    storage: S3StorageClient,
) -> SkillPatchDownloadData:
    asset_repo = MarketAssetRepository(db)
    patch_repo = SkillPatchRepository(db)
    skill = _ensure_skill_asset(repo=asset_repo, skill_asset_id=skill_asset_id)
    patch = patch_repo.get_patch(skill_asset_id=skill.asset_id, patch_version=patch_version)
    if not patch:
        raise PublishError(
            code=404,
            error="skill_patch_not_found",
            message=f"Skill '{skill.name}' 不存在自演进版本 '{patch_version}'",
        )

    key = _patch_zip_key_from_row(storage=storage, skill=skill, patch=patch)
    head = storage.head_object(key)
    if not head.get("success"):
        if head.get("not_found"):
            raise PublishError(
                code=404,
                error="skill_patch_artifact_not_found",
                message="Skill 自演进版本文件不存在或已被删除",
            )
        raise PublishError(
            code=500,
            error="storage_error",
            message=f"读取 Skill 自演进包元数据失败: {head.get('error', 'unknown')}",
        )

    metadata = head.get("metadata") or {}
    checksum_sha256 = str(metadata.get("sha256") or "").strip()
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
    if size is None or not checksum_sha256:
        raise PublishError(
            code=500,
            error="storage_error",
            message="Skill 自演进包对象缺少必要的元数据（sha256/size），请重新发布该版本",
        )

    return SkillPatchDownloadData(
        download_url=storage.presigned_get_url(key),
        skill_asset_id=skill.asset_id,
        patch_id=patch.patch_id,
        patch_version=patch.patch_version,
        file_size=int(size),
        checksum_sha256=checksum_sha256,
    )
