from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skill_review.model.types import SemanticReviewClient


@dataclass(frozen=True)
class LocalArchiveSource:
    local_path: Path


@dataclass(frozen=True)
class SkillReviewOptions:
    semantic_client: SemanticReviewClient | None = None
    include_debug_artifacts: bool = False
