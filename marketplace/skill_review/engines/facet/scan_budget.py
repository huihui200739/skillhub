from __future__ import annotations

from dataclasses import dataclass
from functools import cmp_to_key

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.facet.coverage_gap import build_coverage_gap_fact
from skill_review.runtime.package_access.file_catalog import compare_review_relevant_paths

DEFAULT_MAX_FILES = 250
DEFAULT_MAX_TOTAL_BYTES = 16 * 1024 * 1024


@dataclass
class SkillFacetScanStats:
    selected_bytes: int = 0
    skipped_by_file_count: int = 0
    skipped_by_byte_budget: int = 0


@dataclass(frozen=True)
class SkillFacetScanPlan:
    files: list[PackageFileEntry]
    coverage_gap_facts: list[SkillCapabilityFact]
    selected_bytes: int
    skipped_by_file_count: int
    skipped_by_byte_budget: int


def plan_skill_facet_scan(files: list[PackageFileEntry]) -> SkillFacetScanPlan:
    stats = SkillFacetScanStats()
    selected_files = select_budgeted_files(files, stats)
    return SkillFacetScanPlan(
        files=selected_files,
        coverage_gap_facts=build_scan_budget_gap_facts(stats, len(files), len(selected_files)),
        selected_bytes=stats.selected_bytes,
        skipped_by_file_count=stats.skipped_by_file_count,
        skipped_by_byte_budget=stats.skipped_by_byte_budget,
    )


def select_budgeted_files(files: list[PackageFileEntry], stats: SkillFacetScanStats) -> list[PackageFileEntry]:
    selected_files: list[PackageFileEntry] = []
    for file in sort_review_relevant_files(files):
        if len(selected_files) >= DEFAULT_MAX_FILES:
            stats.skipped_by_file_count += 1
            continue
        if stats.selected_bytes + max(0, file.size) > DEFAULT_MAX_TOTAL_BYTES:
            stats.skipped_by_byte_budget += 1
            continue
        selected_files.append(file)
        stats.selected_bytes += max(0, file.size)
    return selected_files


def sort_review_relevant_files(files: list[PackageFileEntry]) -> list[PackageFileEntry]:
    return sorted(
        files,
        key=cmp_to_key(lambda left, right: compare_review_relevant_paths(left.path, right.path)),
    )


def build_scan_budget_gap_facts(
    stats: SkillFacetScanStats,
    eligible_file_count: int,
    selected_file_count: int,
) -> list[SkillCapabilityFact]:
    skipped_file_count = stats.skipped_by_file_count + stats.skipped_by_byte_budget
    if skipped_file_count == 0:
        return []
    detail = "; ".join(
        [
            f"skill facet scan selected {selected_file_count} of {eligible_file_count} files",
            f"skipped_by_file_count={stats.skipped_by_file_count}",
            f"skipped_by_byte_budget={stats.skipped_by_byte_budget}",
        ]
    )
    return [
        build_coverage_gap_fact(
            extractor_id="skill-facet-scan-budget",
            file_path="__facet_scan_budget__",
            affects=["external_network", "third_party_dependencies"],
            reason="scan_budget_exceeded",
            detail=detail,
        )
    ]
