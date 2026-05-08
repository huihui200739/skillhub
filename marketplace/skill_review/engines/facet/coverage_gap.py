from __future__ import annotations

from skill_review.domain.system_facets import SkillCapabilityFact, SkillFacetArea, SkillCoverageGapReason
from skill_review.runtime.package_access.base import PackageTextReadResult
from skill_review.runtime.package_summary.types import PackageSummary
from skill_review.engines.facet.fact_id import build_fact_id, build_merge_key


def build_coverage_gap_fact(
    *,
    extractor_id: str,
    file_path: str,
    affects: list[SkillFacetArea],
    reason: SkillCoverageGapReason,
    detail: str,
) -> SkillCapabilityFact:
    subject = f"{'+'.join(sorted(affects))}:{file_path}"
    return {
        "fact_id": build_fact_id(
            kind="coverage_gap",
            extractor_id=extractor_id,
            subject=subject,
            file=file_path,
            line=1,
        ),
        "merge_key": build_merge_key(
            kind="coverage_gap",
            subject=subject,
            phase="unknown",
            scope=reason,
        ),
        "kind": "coverage_gap",
        "source": "coverage",
        "confidence": "low",
        "subject": subject,
        "phase": "unknown",
        "evidence": [{"file": file_path, "line": 1, "text": detail}],
        "coverage_gap": {
            "affects": affects,
            "reason": reason,
            "detail": detail,
        },
    }


def build_read_coverage_gap_fact(
    *,
    extractor_id: str,
    file_path: str,
    affects: list[SkillFacetArea],
    read_result: PackageTextReadResult,
) -> SkillCapabilityFact | None:
    if read_result.truncated:
        return build_coverage_gap_fact(
            extractor_id=extractor_id,
            file_path=file_path,
            affects=affects,
            reason="file_too_large",
            detail=f"{file_path} exceeds safe read limits for static facet analysis",
        )
    if read_result.error:
        return build_coverage_gap_fact(
            extractor_id=extractor_id,
            file_path=file_path,
            affects=affects,
            reason=map_read_error_to_coverage_reason(read_result.error),
            detail=read_result.error,
        )
    if read_result.content is None:
        return build_coverage_gap_fact(
            extractor_id=extractor_id,
            file_path=file_path,
            affects=affects,
            reason="unsupported_format",
            detail=f"{file_path} could not be read as analyzable text",
        )
    return None


def build_package_summary_coverage_gap_facts(package_summary: PackageSummary) -> list[SkillCapabilityFact]:
    if package_summary.inspection_level == "deep":
        return []
    detail = "；".join(package_summary.warnings) or "package inspection is incomplete"
    reason = "unsupported_format" if package_summary.archive_format == "unknown" else "parse_failed"
    return [
        build_coverage_gap_fact(
            extractor_id="skill-facet-engine",
            file_path="__package__",
            affects=["external_network", "third_party_dependencies"],
            reason=reason,
            detail=detail,
        )
    ]


def fact_affects_area(fact: SkillCapabilityFact, area: SkillFacetArea) -> bool:
    coverage_gap = fact.get("coverage_gap")
    return (
        fact.get("kind") == "coverage_gap"
        and isinstance(coverage_gap, dict)
        and area in coverage_gap.get("affects", [])
    )


def map_read_error_to_coverage_reason(error: str) -> SkillCoverageGapReason:
    if error in {"unsupported_archive_format", "not_text_like"}:
        return "unsupported_format"
    return "parse_failed"
