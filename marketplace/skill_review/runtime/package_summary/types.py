from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from skill_review.domain.types import PackageFileEntry

InspectionLevel = Literal["deep", "partial"]


@dataclass(frozen=True)
class PackageSummary:
    archive_format: str
    inspection_level: InspectionLevel
    files: list[PackageFileEntry]
    warnings: list[str]
