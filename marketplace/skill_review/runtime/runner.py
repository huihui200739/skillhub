from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from skill_review.aggregation.review_aggregator import aggregate_review_result
from skill_review.domain.types import ReviewFindingDraft, SkillReviewExecution, SkillReviewInput
from skill_review.engines.registry import create_default_review_engine_registry
from skill_review.engines.semantic.adjudication import apply_semantic_candidate_reviews
from skill_review.engines.types import ReviewEngineContext, ReviewEngineRegistry, ReviewEngineResult
from skill_review.runtime.package_access.base import PackageAccess
from skill_review.runtime.options import LocalArchiveSource, SkillReviewOptions
from skill_review.runtime.package_access.zip_access import open_zip_package
from skill_review.runtime.package_summary.summary_builder import build_package_summary

REVIEW_POLICY_VERSION = "skill-review-v3"


def run_skill_review(
    *,
    review_input: SkillReviewInput,
    archive: LocalArchiveSource,
    options: SkillReviewOptions | None = None,
    registry: ReviewEngineRegistry | None = None,
) -> SkillReviewExecution:
    resolved_options = options or SkillReviewOptions()
    resolved_registry = registry or create_default_review_engine_registry()
    trace_id = uuid.uuid4().hex
    access = open_zip_package(Path(archive.local_path))
    try:
        package_summary = build_package_summary(access)
        deterministic_results = _run_engines(
            resolved_registry.deterministic_engines,
            review_input=review_input,
            package_access=access,
            package_summary=package_summary,
            options=resolved_options,
            prior_findings=[],
            prior_artifacts={},
        )
        deterministic_findings = _collect_findings(deterministic_results)
        deterministic_artifacts = _collect_artifacts(deterministic_results)
        semantic_results = _run_engines(
            resolved_registry.semantic_engines,
            review_input=review_input,
            package_access=access,
            package_summary=package_summary,
            options=resolved_options,
            prior_findings=deterministic_findings,
            prior_artifacts=deterministic_artifacts,
        )
        semantic_raw_output = _find_raw_output(semantic_results, "semantic")
        applied_candidate_reviews = apply_semantic_candidate_reviews(
            deterministic_findings,
            semantic_raw_output.get("response") if isinstance(semantic_raw_output, dict) else None,
        )
        final_deterministic_findings = applied_candidate_reviews["final_findings"]
        all_findings = [*final_deterministic_findings, *_collect_findings(semantic_results)]
        review_result = aggregate_review_result(all_findings)
        return SkillReviewExecution(
            review_result=review_result,
            raw_output=_build_raw_output(
                trace_id=trace_id,
                package_summary=package_summary,
                deterministic_results=deterministic_results,
                semantic_results=semantic_results,
                review_result=review_result,
                applied_candidate_reviews=applied_candidate_reviews,
            ),
            review_mode="hybrid" if resolved_options.semantic_client is not None else "deterministic_only",
            review_engine=str(semantic_raw_output.get("engine") or "deterministic-only"),
            model_name=(
                semantic_raw_output.get("model_name")
                if isinstance(semantic_raw_output.get("model_name"), str)
                else None
            ),
            policy_version=REVIEW_POLICY_VERSION,
            trace_id=trace_id,
        )
    finally:
        access.close()


def _run_engines(
    engines: list[Any],
    *,
    review_input: SkillReviewInput,
    package_access: PackageAccess,
    package_summary: Any,
    options: SkillReviewOptions,
    prior_findings: list[ReviewFindingDraft],
    prior_artifacts: dict[str, Any],
) -> list[ReviewEngineResult]:
    results: list[ReviewEngineResult] = []
    findings = list(prior_findings)
    artifacts = dict(prior_artifacts)
    for engine in engines:
        context = ReviewEngineContext(
            input=review_input,
            package_access=package_access,
            package_summary=package_summary,
            options=options,
            prior_findings=findings,
            prior_artifacts=artifacts,
        )
        result = engine.run(context)
        results.append(result)
        findings.extend(result.findings)
        artifacts.update(result.artifacts)
    return results


def _collect_findings(results: list[ReviewEngineResult]) -> list[ReviewFindingDraft]:
    return [finding for result in results for finding in result.findings]


def _collect_artifacts(results: list[ReviewEngineResult]) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for result in results:
        artifacts.update(result.artifacts)
    return artifacts


def _find_raw_output(results: list[ReviewEngineResult], kind: str) -> dict[str, Any]:
    for result in results:
        if result.kind == kind and isinstance(result.raw_output, dict):
            return result.raw_output
    return {}


def _build_raw_output(
    *,
    trace_id: str,
    package_summary: Any,
    deterministic_results: list[ReviewEngineResult],
    semantic_results: list[ReviewEngineResult],
    review_result: dict[str, Any],
    applied_candidate_reviews: dict[str, Any],
) -> dict[str, Any]:
    semantic_output = _find_raw_output(semantic_results, "semantic")
    skill_facet_output = _find_raw_output(deterministic_results, "facet")
    behavior_output = _find_raw_output(deterministic_results, "behavior")
    rule_output = _find_raw_output(deterministic_results, "rule")
    return {
        "observability": {
            "trace_id": trace_id,
            "policy_version": REVIEW_POLICY_VERSION,
            "review_mode": "hybrid" if semantic_output.get("model_name") else "deterministic_only",
            "review_engine": semantic_output.get("engine") or "deterministic-only",
        },
        "package": {
            "archive_format": package_summary.archive_format,
            "inspection_level": package_summary.inspection_level,
            "file_count": len(package_summary.files),
            "files": [{"path": item.path, "size": item.size} for item in package_summary.files],
            "warnings": package_summary.warnings,
        },
        "engine_coverage": [
            coverage for result in [*deterministic_results, *semantic_results] for coverage in result.coverage
        ],
        "merged_review": {
            "overall": review_result["overall"],
            "risk": review_result["risk"],
            "metrics": review_result["metrics"],
            "sections": review_result["sections"],
        },
        "semantic_candidate_review": {
            "candidate_reviews": applied_candidate_reviews["candidate_reviews"],
            "suppressed_finding_count": len(applied_candidate_reviews["suppressed_findings"]),
        },
        **skill_facet_output,
        **behavior_output,
        **rule_output,
        "semantic_review": semantic_output,
    }
