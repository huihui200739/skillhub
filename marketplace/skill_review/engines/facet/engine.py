from __future__ import annotations

from skill_review.engines.facet.pipeline import run_skill_facet_pipeline
from skill_review.engines.types import ReviewEngine, ReviewEngineContext, ReviewEngineResult


def _run_skill_facet_engine(context: ReviewEngineContext) -> ReviewEngineResult:
    facets, inventory = run_skill_facet_pipeline(context.package_access, context.package_summary)
    coverage_status = "unavailable" if "analysis_incomplete" in facets["system_tags"] else "completed"
    scan = inventory.get("scan") if isinstance(inventory.get("scan"), dict) else {}
    eligible_file_count = int(scan.get("eligible_file_count") or len(context.package_summary.files))
    scanned_file_count = int(scan.get("scanned_file_count") or 0)
    return ReviewEngineResult(
        engine_id="skill-facet",
        engine_name="Skill System Facet Engine",
        kind="facet",
        coverage=[
            {
                "engine_id": "skill-facet",
                "engine_name": "Skill System Facet Engine",
                "status": coverage_status,
                **(coverage_reason(facets) if coverage_status == "unavailable" else {}),
                "eligible_file_count": eligible_file_count,
                "scanned_file_count": scanned_file_count,
                "skipped_by_file_count": int(scan.get("skipped_by_file_count") or 0),
                "skipped_by_byte_budget": int(scan.get("skipped_by_byte_budget") or 0),
                "finding_count": 0,
            }
        ],
        findings=[],
        artifacts={
            "skill_capability_facts": inventory["facts"],
            "skill_system_facets": facets,
        },
        raw_output={
            "skill_facets": facets,
            "capability_inventory": inventory,
        },
    )


def coverage_reason(facets: dict) -> dict[str, str]:
    incomplete_coverage = [record for record in facets.get("coverage", []) if record.get("status") != "completed"]
    if not incomplete_coverage:
        return {"reason": "analysis_incomplete"}
    return {
        "reason": ", ".join(
            [
                f"{record.get('analyzer_id')}:{record.get('reason')}"
                if record.get("reason")
                else str(record.get("analyzer_id"))
                for record in incomplete_coverage
            ]
        )
    }


skill_facet_engine = ReviewEngine(
    engine_id="skill-facet",
    engine_name="Skill System Facet Engine",
    kind="facet",
    failure_policy="abort_review",
    run=_run_skill_facet_engine,
)
