from __future__ import annotations

from skill_review.engines.behavior.engine import behavior_facts_engine
from skill_review.engines.facet.engine import skill_facet_engine
from skill_review.engines.rule.engine import rule_engine
from skill_review.engines.semantic.engine import semantic_engine
from skill_review.engines.system import system_engine
from skill_review.engines.types import ReviewEngineRegistry


def create_default_review_engine_registry() -> ReviewEngineRegistry:
    return ReviewEngineRegistry(
        deterministic_engines=[system_engine, skill_facet_engine, behavior_facts_engine, rule_engine],
        semantic_engines=[semantic_engine],
    )
