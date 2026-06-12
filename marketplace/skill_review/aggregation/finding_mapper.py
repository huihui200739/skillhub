from __future__ import annotations

from dataclasses import asdict
from typing import Any

from skill_review.domain.types import ReviewFindingDraft


def serialize_review_finding(finding: ReviewFindingDraft) -> dict[str, Any]:
    return asdict(finding)
