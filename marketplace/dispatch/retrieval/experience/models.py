from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ExperienceItem:
    """One entry in the experience knowledge base.

    Each item represents a class of queries that map to one or more skills.
    """

    id: str
    query_pattern: str = ""
    query_examples: list[str] = field(default_factory=list)
    skill_ids: list[str] = field(default_factory=list)
    success_count: int = 1
    embedding: list[float] = field(default_factory=list)
    created_at: float = 0.0
    last_hit_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query_pattern": self.query_pattern,
            "query_examples": self.query_examples,
            "skill_ids": self.skill_ids,
            "success_count": self.success_count,
            "embedding": self.embedding,
            "created_at": self.created_at,
            "last_hit_at": self.last_hit_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExperienceItem:
        return cls(
            id=data["id"],
            query_pattern=data.get("query_pattern", ""),
            query_examples=list(data.get("query_examples", [])),
            skill_ids=list(data.get("skill_ids", [])),
            success_count=int(data.get("success_count", 1)),
            embedding=list(data.get("embedding", [])),
            created_at=float(data.get("created_at", 0.0)),
            last_hit_at=float(data.get("last_hit_at", 0.0)),
        )


@dataclass
class QuerySkillRecord:
    """A raw query-skill pair collected from a successful tree search."""

    query: str
    skill_ids: list[str]
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
