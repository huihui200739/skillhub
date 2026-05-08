from __future__ import annotations

from functools import cmp_to_key

from skill_review.domain.types import PackageFileEntry
from skill_review.runtime.package_access.base import PackageAccess
from skill_review.runtime.package_access.file_catalog import (
    compare_review_relevant_paths,
    is_summary_anchor_file,
    is_text_like_file,
)
from skill_review.runtime.package_summary.types import PackageSummary

MAX_DEEP_REVIEW_TEXT_FILES = 1000
MAX_SUMMARY_PROBE_FILES = 12
SUMMARY_PROBE_MAX_BYTES = 8 * 1024


def build_package_summary(access: PackageAccess) -> PackageSummary:
    files = access.list_files()
    warnings = collect_package_summary_warnings(access, files)
    return PackageSummary(
        archive_format=access.archive_format,
        inspection_level="partial" if warnings else "deep",
        files=files,
        warnings=warnings,
    )


def collect_package_summary_warnings(access: PackageAccess, files: list[PackageFileEntry]) -> list[str]:
    warnings: list[str] = []
    text_like_files = [file for file in files if is_text_like_file(file.path)]

    if len(text_like_files) > MAX_DEEP_REVIEW_TEXT_FILES:
        warnings.append(
            f"可审查文本文件数量 {len(text_like_files)} 超过可信深度审查上限 "
            f"{MAX_DEEP_REVIEW_TEXT_FILES}，系统将按不完整覆盖处理。"
        )

    unreadable_anchor_files = find_unreadable_anchor_files(access, files)
    if unreadable_anchor_files:
        warnings.append(f"关键审查文件不可读：{'、'.join(unreadable_anchor_files)}")

    return warnings


def find_unreadable_anchor_files(access: PackageAccess, files: list[PackageFileEntry]) -> list[str]:
    anchor_files = sorted(
        [file for file in files if is_summary_anchor_file(file.path)],
        key=cmp_to_key(compare_package_files_by_review_relevance),
    )[:MAX_SUMMARY_PROBE_FILES]
    unreadable_files: list[str] = []

    for file in anchor_files:
        read_result = access.read_text_file(file.path, max_bytes=SUMMARY_PROBE_MAX_BYTES)
        if read_result.status != "success" or read_result.content is None:
            unreadable_files.append(file.path)
    return unreadable_files


def compare_package_files_by_review_relevance(left: PackageFileEntry, right: PackageFileEntry) -> int:
    return compare_review_relevant_paths(left.path, right.path)
