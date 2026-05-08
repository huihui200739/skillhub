from __future__ import annotations

from collections import defaultdict
from typing import Any

from skill_review.domain.system_facets import MergedSkillCapabilityFact, SkillCapabilityFact

DEPENDENCY_PRIORITY = ["lockfile", "manifest", "ast_import", "command", "documentation", "scanner_supplement"]
NETWORK_PRIORITY = [
    "entrypoint",
    "ast_sink_with_literal_endpoint",
    "command_with_endpoint",
    "ast_sink_dynamic",
    "documentation",
    "scanner_supplement",
]


def merge_skill_capability_facts(facts: list[SkillCapabilityFact]) -> list[MergedSkillCapabilityFact]:
    grouped_facts: dict[str, list[SkillCapabilityFact]] = defaultdict(list)
    for fact in facts:
        grouped_facts[str(fact["merge_key"])].append(fact)
    return sorted(
        [build_merged_fact(merge_key, group) for merge_key, group in grouped_facts.items()],
        key=lambda item: item["merge_key"],
    )


def build_merged_fact(merge_key: str, facts: list[SkillCapabilityFact]) -> MergedSkillCapabilityFact:
    primary_fact = sorted(facts, key=source_score, reverse=True)[0]
    return {
        "merge_key": merge_key,
        "kind": primary_fact["kind"],
        "subject": primary_fact["subject"],
        "primary_source": primary_fact["source"],
        "confidence": max_confidence([str(fact["confidence"]) for fact in facts]),
        "fact_ids": [fact["fact_id"] for fact in facts],
        "evidence_refs": [evidence for fact in facts for evidence in fact.get("evidence", [])],
        "facts": facts,
    }


def source_score(fact: SkillCapabilityFact) -> int:
    priority = NETWORK_PRIORITY if fact["kind"] in {"network_call", "endpoint"} else DEPENDENCY_PRIORITY
    try:
        return len(priority) - priority.index(str(fact["source"]))
    except ValueError:
        return 0


def max_confidence(values: list[str]) -> str:
    if "high" in values:
        return "high"
    if "medium" in values:
        return "medium"
    return "low"
