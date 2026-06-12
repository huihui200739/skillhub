from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from skill_review.domain.types import ReviewFindingDraft, SkillReviewInput
from skill_review.runtime.options import SkillReviewOptions
from skill_review.runtime.package_access.base import PackageAccess
from skill_review.runtime.package_summary.types import PackageSummary

EngineKind = Literal["system", "facet", "behavior", "rule", "semantic"]
FailurePolicy = Literal["abort_review", "allow_optional_failure"]


@dataclass(frozen=True)
class ReviewEngineContext:
    input: SkillReviewInput
    package_access: PackageAccess
    package_summary: PackageSummary
    options: SkillReviewOptions
    prior_findings: list[ReviewFindingDraft] = field(default_factory=list)
    prior_artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewEngineResult:
    engine_id: str
    engine_name: str
    kind: EngineKind
    coverage: list[dict[str, Any]]
    findings: list[ReviewFindingDraft]
    artifacts: dict[str, Any] = field(default_factory=dict)
    raw_output: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReviewEngine:
    engine_id: str
    engine_name: str
    kind: EngineKind
    failure_policy: FailurePolicy
    run: Callable[[ReviewEngineContext], ReviewEngineResult]


@dataclass(frozen=True)
class ReviewEngineRegistry:
    deterministic_engines: list[ReviewEngine]
    semantic_engines: list[ReviewEngine]
