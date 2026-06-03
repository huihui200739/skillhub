from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import operation_log_fields
from plugins_market.core.skill_model_client import create_skill_review_semantic_client
from skill_review import (
    LocalArchiveSource,
    SkillReviewExecution,
    SkillReviewInput,
    SkillReviewOptions,
    run_skill_review as run_core_skill_review,
)
from skill_review.model.openai_compatible import SkillReviewSemanticRuntimeError

REVIEW_POLICY_VERSION = "skill-review-v3"
logger = get_logger(__name__)


def run_skill_review(*, asset: Any, version_row: Any, storage: Any) -> SkillReviewExecution:
    asset_id = str(getattr(asset, "asset_id", "") or "").strip() or None
    version = str(getattr(version_row, "version", "") or "").strip() or None
    package_name = build_package_name(asset, version_row)
    logger.info(
        "skill review runtime",
        **operation_log_fields(
            stage="runtime_prepare",
            result="started",
            asset_id=asset_id,
            version=version,
            package_name=package_name,
        ),
    )
    archive_key = resolve_archive_key(storage, version_row.file_path, package_name)
    logger.info(
        "skill review runtime",
        **operation_log_fields(
            stage="resolve_archive",
            result="success",
            asset_id=asset_id,
            version=version,
            archive_key=archive_key,
        ),
    )
    semantic_client = create_skill_review_semantic_client()
    if semantic_client is None:
        logger.warning(
            "skill review runtime",
            **operation_log_fields(
                stage="create_semantic_client",
                result="failure",
                error_code="skill_review_model_config_missing",
                error_class="upstream",
                asset_id=asset_id,
                version=version,
            ),
        )
        raise SkillReviewSemanticRuntimeError("skill review semantic model config missing")
    logger.info(
        "skill review runtime",
        **operation_log_fields(stage="create_semantic_client", result="success", asset_id=asset_id, version=version),
    )

    with tempfile.TemporaryDirectory(prefix="skill_review_") as temp_dir:
        local_archive_path = Path(temp_dir) / package_name
        logger.info(
            "skill review runtime",
            **operation_log_fields(
                stage="download_archive",
                result="started",
                asset_id=asset_id,
                version=version,
                archive_key=archive_key,
            ),
        )
        download_archive(storage, archive_key, local_archive_path)
        package_size = local_archive_path.stat().st_size
        logger.info(
            "skill review runtime",
            **operation_log_fields(
                stage="download_archive",
                result="success",
                asset_id=asset_id,
                version=version,
                archive_key=archive_key,
                package_size=package_size,
            ),
        )
        logger.info(
            "skill review runtime",
            **operation_log_fields(stage="execute_core_review", result="started", asset_id=asset_id, version=version),
        )
        execution = run_core_skill_review(
            review_input=SkillReviewInput(
                skill_name=str(getattr(asset, "name", "") or "").strip(),
                category=str(getattr(asset, "plugin_type", "") or "").strip() or "skill",
                description=str(getattr(asset, "detail_desc", None) or getattr(asset, "short_desc", "") or "").strip(),
                tags=getattr(asset, "tags", []) if isinstance(getattr(asset, "tags", []), list) else [],
                package_name=package_name,
                package_size=package_size,
            ),
            archive=LocalArchiveSource(local_path=local_archive_path),
            options=SkillReviewOptions(semantic_client=semantic_client),
        )
        logger.info(
            "skill review runtime",
            **operation_log_fields(
                stage="execute_core_review",
                result="success",
                asset_id=asset_id,
                version=version,
                review_mode=getattr(execution, "review_mode", None),
                review_engine=getattr(execution, "review_engine", None),
                trace_id=getattr(execution, "trace_id", None),
            ),
        )
        return execution


def build_package_name(asset: Any, version_row: Any) -> str:
    safe_name = str(getattr(asset, "name", "") or "skill").strip().replace(" ", "-")
    version = str(getattr(version_row, "version", "") or "0.0.0").strip()
    return f"{safe_name}_{version}.zip"


def resolve_archive_key(storage: Any, file_path: str | None, package_name: str) -> str:
    bucket_name = str(getattr(getattr(storage, "config", None), "bucket_name", "") or "")
    key = resolve_storage_key(file_path, bucket_name)
    if not key:
        raise RuntimeError("无法解析 Skill 包存储路径")
    if key.endswith("/"):
        return f"{key}{package_name}"
    if not key.lower().endswith(".zip"):
        return f"{key.rstrip('/')}/{package_name}"
    return key


def resolve_storage_key(file_path: str | None, bucket_name: str) -> str | None:
    raw = (file_path or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        return raw
    parsed = urlparse(raw)
    path = (parsed.path or "").lstrip("/")
    if bucket_name and path.startswith(f"{bucket_name}/"):
        return path[len(bucket_name) + 1:]
    return path


def download_archive(storage: Any, archive_key: str, local_archive_path: Path) -> None:
    response = storage.s3_client.get_object(Bucket=storage.config.bucket_name, Key=archive_key)
    body = response.get("Body")
    if body is None:
        raise RuntimeError("Skill 包对象内容为空")
    local_archive_path.write_bytes(body.read())
