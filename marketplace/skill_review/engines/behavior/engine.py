from __future__ import annotations

from skill_review.engines.behavior.pipeline import run_behavior_facts_pipeline
from skill_review.engines.types import ReviewEngine, ReviewEngineContext, ReviewEngineResult


def _run_behavior_facts_engine(context: ReviewEngineContext) -> ReviewEngineResult:
    inventory = run_behavior_facts_pipeline(context.package_access)
    coverage = map_behavior_coverage(inventory)
    return ReviewEngineResult(
        engine_id="behavior-facts",
        engine_name="Behavior Facts Engine",
        kind="behavior",
        coverage=[coverage],
        findings=[],
        artifacts={
            "review_behavior_facts": inventory["facts"],
        },
        raw_output={
            "behavior_inventory": inventory,
        },
    )


def map_behavior_coverage(inventory: dict) -> dict:
    coverage = inventory["coverage"][0] if inventory.get("coverage") else {}
    is_complete = coverage.get("status") == "completed"
    return {
        "engine_id": "behavior-facts",
        "engine_name": "Behavior Facts Engine",
        "status": "completed" if is_complete else "unavailable",
        **({"reason": coverage["reason"]} if coverage.get("reason") else {}),
        "eligible_file_count": coverage.get("eligible_file_count", 0),
        "scanned_file_count": coverage.get("scanned_file_count", 0),
        "skipped_by_budget_count": coverage.get("skipped_file_count", 0),
        "truncated_file_count": coverage.get("truncated_file_count", 0),
        "unreadable_file_count": coverage.get("unreadable_file_count", 0),
        "finding_count": 0,
    }


behavior_facts_engine = ReviewEngine(
    engine_id="behavior-facts",
    engine_name="Behavior Facts Engine",
    kind="behavior",
    failure_policy="abort_review",
    run=_run_behavior_facts_engine,
)
