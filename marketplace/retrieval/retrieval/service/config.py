from __future__ import annotations

from dataclasses import dataclass, field

from retrieval.lexical.bm25 import BM25FinderConfig
from retrieval.semantic.embedding import EmbeddingFinderConfig
from retrieval.service.models import HybridFusionConfig
from retrieval.tree.progressive import ProgressiveFinderConfig


FinderMethod = str


@dataclass(frozen=True)
class FinderConfig:
    method: FinderMethod = "auto"
    top_k: int = 10
    bm25: BM25FinderConfig = field(default_factory=BM25FinderConfig)
    progressive: ProgressiveFinderConfig = field(default_factory=ProgressiveFinderConfig)
    embedding: EmbeddingFinderConfig = field(default_factory=EmbeddingFinderConfig)
    hybrid: HybridFusionConfig = field(default_factory=HybridFusionConfig)


__all__ = ["FinderConfig", "FinderMethod"]
