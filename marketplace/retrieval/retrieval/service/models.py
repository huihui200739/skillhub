# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List

from retrieval.lexical.bm25 import BM25FinderConfig
from retrieval.semantic.embedding import EmbeddingFinderConfig
from retrieval.tree.progressive import ProgressiveFinderConfig


class RetrievalMethod(str, Enum):
    AUTO = "auto"
    BM25 = "bm25"
    EMBEDDING = "embedding"
    PROGRESSIVE = "progressive"


class HybridFusionMethod(str, Enum):
    RRF = "rrf"
    RELATIVE_SCORE = "relative_score"


@dataclass(frozen=True)
class HybridFusionConfig:
    method: str = HybridFusionMethod.RRF.value
    rrf_k: int = 60
    embedding_weight: float = 1.0
    bm25_weight: float = 1.0


@dataclass(frozen=True)
class SearchConfig:
    top_k: int
    method: RetrievalMethod = RetrievalMethod.AUTO
    llm_top_k: int | None = None
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_delta: float = 0.5
    bm25_min_score: float | None = None
    bm25_relative_min_score: float | None = None
    bm25_autocut_jumps: int = 0
    bm25_autocut_min_relative_drop: float = 0.0
    bm25_min_query_term_matches: int = 0
    bm25_min_query_term_match_ratio: float = 0.0
    embedding_batch_size: int = 64
    embedding_min_score: float | None = None
    embedding_relative_min_score: float | None = None
    embedding_autocut_jumps: int = 0
    embedding_autocut_min_relative_drop: float = 0.0
    hybrid_fusion_method: HybridFusionMethod = HybridFusionMethod.RRF
    hybrid_rrf_k: int = 60
    hybrid_embedding_weight: float = 1.0
    hybrid_bm25_weight: float = 1.0
    progressive_batch_size: int = 1
    progressive_max_tokens: int = 48
    progressive_request_timeout: float | None = None
    trie_constrained_decoding_enabled: bool = False
    trie_constraint_allow_user_nodes: bool = True
    trie_constraint_max_candidates: int = 512
    trie_constraint_fallback_payload: str = ""
    progressive_max_branch_choices: int = 6
    progressive_auto_expand_child_threshold: int = 3
    progressive_collapse_single_chain: bool = True
    progressive_max_collapse_steps: int = 8
    progressive_max_parallel_branches: int = 3
    progressive_enable_parallel_branches: bool = True
    progressive_auto_terminal_item_threshold: int = 12
    progressive_branch_choice_slack: int = 2
    progressive_branch_candidate_slack: int = 1
    progressive_round_robin_branch_reduce: bool = True
    progressive_branch_max_tokens: int = 96
    progressive_item_max_tokens: int = 128
    progressive_compact_boundary_codes_enabled: bool = False
    progressive_max_exposure_depth_per_call: int = 2
    progressive_exposure_threshold: int = 12
    progressive_force_expand_single_child: bool = True


@dataclass(frozen=True)
class RetrieverConfig:
    method: str = "auto"
    top_k: int = 10
    bm25: BM25FinderConfig = field(default_factory=BM25FinderConfig)
    embedding: EmbeddingFinderConfig = field(default_factory=EmbeddingFinderConfig)
    progressive: ProgressiveFinderConfig = field(default_factory=ProgressiveFinderConfig)
    hybrid: HybridFusionConfig = field(default_factory=HybridFusionConfig)


@dataclass
class RetrieverSearchResult:
    method: str
    payloads: List[str]
    candidate_records: List[Dict[str, object]]
    summary_lines: List[str]
    selected_payload: str | None
    selected_rank: int
    elapsed_ms: float = 0.0
    trace_events: List[Dict[str, object]] = field(default_factory=list)


def runtime_retriever_config_from_search(config: SearchConfig) -> RetrieverConfig:
    resolved_top_k = max(1, int(config.top_k))
    return RetrieverConfig(
        method=str(config.method.value),
        top_k=resolved_top_k,
        bm25=BM25FinderConfig(
            top_k=resolved_top_k,
            k1=float(config.bm25_k1),
            b=float(config.bm25_b),
            delta=float(config.bm25_delta),
            min_score=config.bm25_min_score,
            relative_min_score=config.bm25_relative_min_score,
            autocut_jumps=max(0, int(config.bm25_autocut_jumps)),
            autocut_min_relative_drop=max(0.0, float(config.bm25_autocut_min_relative_drop)),
            min_query_term_matches=max(0, int(config.bm25_min_query_term_matches)),
            min_query_term_match_ratio=max(0.0, float(config.bm25_min_query_term_match_ratio)),
        ),
        embedding=EmbeddingFinderConfig(
            top_k=resolved_top_k,
            batch_size=max(1, int(config.embedding_batch_size)),
            min_score=config.embedding_min_score,
            relative_min_score=config.embedding_relative_min_score,
            autocut_jumps=max(0, int(config.embedding_autocut_jumps)),
            autocut_min_relative_drop=max(0.0, float(config.embedding_autocut_min_relative_drop)),
        ),
        hybrid=HybridFusionConfig(
            method=str(config.hybrid_fusion_method.value),
            rrf_k=max(1, int(config.hybrid_rrf_k)),
            embedding_weight=max(0.0, float(config.hybrid_embedding_weight)),
            bm25_weight=max(0.0, float(config.hybrid_bm25_weight)),
        ),
        progressive=ProgressiveFinderConfig(
            top_k=resolved_top_k,
            batch_size=max(1, int(config.progressive_batch_size)),
            max_tokens=max(1, int(config.progressive_max_tokens)),
            trie_constrained_decoding_enabled=bool(config.trie_constrained_decoding_enabled),
            trie_constraint_allow_user_nodes=bool(config.trie_constraint_allow_user_nodes),
            trie_constraint_max_candidates=max(1, int(config.trie_constraint_max_candidates)),
            trie_constraint_fallback_payload=str(config.trie_constraint_fallback_payload or ""),
            max_branch_choices=max(1, int(config.progressive_max_branch_choices)),
            auto_expand_child_threshold=max(1, int(config.progressive_auto_expand_child_threshold)),
            collapse_single_chain=bool(config.progressive_collapse_single_chain),
            max_collapse_steps=max(1, int(config.progressive_max_collapse_steps)),
            max_parallel_branches=max(1, int(config.progressive_max_parallel_branches)),
            enable_parallel_branches=bool(config.progressive_enable_parallel_branches),
            auto_terminal_item_threshold=max(1, int(config.progressive_auto_terminal_item_threshold)),
            branch_choice_slack=max(0, int(config.progressive_branch_choice_slack)),
            branch_candidate_slack=max(0, int(config.progressive_branch_candidate_slack)),
            round_robin_branch_reduce=bool(config.progressive_round_robin_branch_reduce),
            branch_max_tokens=max(1, int(config.progressive_branch_max_tokens)),
            item_max_tokens=max(1, int(config.progressive_item_max_tokens)),
            request_timeout=config.progressive_request_timeout,
            compact_boundary_codes_enabled=bool(config.progressive_compact_boundary_codes_enabled),
            max_exposure_depth_per_call=max(0, int(config.progressive_max_exposure_depth_per_call)),
            exposure_threshold=max(0, int(config.progressive_exposure_threshold)),
            force_expand_single_child=bool(config.progressive_force_expand_single_child),
        ),
    )


__all__ = [
    "HybridFusionConfig",
    "HybridFusionMethod",
    "RetrievalMethod",
    "RetrieverConfig",
    "RetrieverSearchResult",
    "SearchConfig",
    "runtime_retriever_config_from_search",
]
