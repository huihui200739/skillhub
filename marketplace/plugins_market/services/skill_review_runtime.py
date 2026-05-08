from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def run_skill_review(*, asset: Any, version_row: Any, storage: Any) -> SkillReviewExecution:
    package_name = build_package_name(asset, version_row)
    archive_key = resolve_archive_key(storage, version_row.file_path, package_name)
    semantic_client = create_skill_review_semantic_client()
    if semantic_client is None:
        raise SkillReviewSemanticRuntimeError("skill review semantic model config missing")

    with tempfile.TemporaryDirectory(prefix="skill_review_") as temp_dir:
        local_archive_path = Path(temp_dir) / package_name
        download_archive(storage, archive_key, local_archive_path)
        return run_core_skill_review(
            review_input=SkillReviewInput(
                skill_name=str(getattr(asset, "name", "") or "").strip(),
                category=str(getattr(asset, "plugin_type", "") or "").strip() or "skill",
                description=str(getattr(asset, "detail_desc", None) or getattr(asset, "short_desc", "") or "").strip(),
                tags=getattr(asset, "tags", []) if isinstance(getattr(asset, "tags", []), list) else [],
                package_name=package_name,
                package_size=local_archive_path.stat().st_size,
            ),
            archive=LocalArchiveSource(local_path=local_archive_path),
            options=SkillReviewOptions(semantic_client=semantic_client),
        )


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
