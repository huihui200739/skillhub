from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticSample:
    path: str
    size: int
    preview: str
    truncated: bool
    priority: int


@dataclass(frozen=True)
class SemanticContext:
    samples: list[SemanticSample]
