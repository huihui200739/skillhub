from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from skill_review.domain.types import ReviewFindingDraft, SkillReviewInput
from skill_review.runtime.package_summary.types import PackageSummary


@dataclass(frozen=True)
class SemanticReviewRequest:
    skill: SkillReviewInput
    package_summary: PackageSummary
    candidate_findings: list[ReviewFindingDraft]
    prompt_payload: dict[str, Any]
    system_prompt: str
    response_json_schema: dict[str, Any]


@dataclass(frozen=True)
class SemanticReviewResult:
    engine: str
    model_name: str | None
    response: dict[str, Any]
    token_usage: dict[str, int] | None = None
    raw_response: Any | None = None


class SemanticReviewClient(Protocol):
    model_name: str | None

    def review(self, request: SemanticReviewRequest) -> SemanticReviewResult:
        ...
