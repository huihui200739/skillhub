from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from skill_review.domain.types import PackageFileEntry


@dataclass(frozen=True)
class PackageTextReadResult:
    path: str
    status: str
    content: str | None
    truncated: bool = False
    error: str | None = None


class PackageAccess(Protocol):
    archive_format: str

    def list_files(self) -> list[PackageFileEntry]:
        ...

    def read_text_file(self, path: str, max_bytes: int) -> PackageTextReadResult:
        ...

    def close(self) -> None:
        ...
