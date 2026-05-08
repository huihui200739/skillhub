"""Standalone Skill Review core.

This package intentionally avoids depending on SkillHub service modules so it
can be embedded by SkillHub or reused by another Skill repository.
"""

from skill_review.domain.types import SkillReviewExecution, SkillReviewInput
from skill_review.runtime.options import LocalArchiveSource, SkillReviewOptions
from skill_review.runtime.runner import run_skill_review

__all__ = [
    "LocalArchiveSource",
    "SkillReviewExecution",
    "SkillReviewInput",
    "SkillReviewOptions",
    "run_skill_review",
]
