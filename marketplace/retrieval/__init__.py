# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public SDK entrypoints for the repository.

This module re-exports canonical package APIs only. It must not depend on
demo/data/tests/training/scripts repository directories.
"""

import os
import sys as _sys
from pathlib import Path as _Path

# Internal sub-modules use bare imports (e.g. "from indexing.bm25 import ...",
# "from shared.storage import ...") that require the retrieval/ directory itself
# on sys.path.  When this package is loaded via a parent directory on sys.path
# (e.g. the project root or /app), add retrieval/ so those imports resolve.
_RETRIEVAL_DIR = str(_Path(__file__).resolve().parent)
if _RETRIEVAL_DIR not in _sys.path:
    _sys.path.append(_RETRIEVAL_DIR)

# Some sub-packages under retrieval/retrieval/ use absolute imports like
# "from retrieval.lexical.bm25 import ...".  When this outer __init__.py is
# loaded (via the project root on sys.path), __path__ only points to this
# outer directory which lacks a "lexical/" subdir.  Append the inner
# retrieval/retrieval/ path so those absolute imports resolve correctly.
_INNER_DIR = os.path.join(_RETRIEVAL_DIR, "retrieval")
if os.path.isdir(_INNER_DIR) and _INNER_DIR not in __path__:
    __path__.append(_INNER_DIR)

from .indexing import (
    BM25Index,
    CatalogRecord,
    EmbeddingClient,
    EmbeddingIndex,
    EmbeddingRecord,
    IndexedEmbeddingRecord,
    OpenAIEmbeddingClient,
    build_bm25_index,
    build_embedding_index,
    create_openai_embedding_client,
    load_bm25_index,
    load_embedding_index,
)
from .indexing.tree import DynamicTreeConfig, TreeBuilder, TreeNode, build_tree
from .indexing.workflows.artifacts import BuildConfig, BuildMethod, IndexBuildRuntimeConfig
from .indexing.workflows.index_builder import IndexBuilder

try:
    from .orchestration.engine.orchestrator import (
        Orchestrator,
        OrchestratorConfig,
        OrchestratorResponse,
        OrchestratorStatus,
        create_orchestrator,
    )
except ImportError:
    Orchestrator = None  # type: ignore[misc,assignment]
    OrchestratorConfig = None  # type: ignore[misc,assignment]
    OrchestratorResponse = None  # type: ignore[misc,assignment]
    OrchestratorStatus = None  # type: ignore[misc,assignment]
    create_orchestrator = None  # type: ignore[misc,assignment]

from .orchestration.llm.client import LLMClient

try:
    from .orchestration.retrieval_adapter import (
        CIDTreeTopKRetriever,
        EmbeddingLeafHit,
        EmbeddingLeafRanker,
        RetrieverConfig as CIDTreeRetrieverConfig,
        RetrieverResult as CIDTreeRetrieverResult,
        build_leaf_embedding_text,
    )
    from .orchestration.runtime import NodeRuntime
except ImportError:
    CIDTreeTopKRetriever = None  # type: ignore[misc,assignment]
    EmbeddingLeafHit = None  # type: ignore[misc,assignment]
    EmbeddingLeafRanker = None  # type: ignore[misc,assignment]
    CIDTreeRetrieverConfig = None  # type: ignore[misc,assignment]
    CIDTreeRetrieverResult = None  # type: ignore[misc,assignment]
    build_leaf_embedding_text = None  # type: ignore[misc,assignment]
    NodeRuntime = None  # type: ignore[misc,assignment]

from .retrieval.io.loading import load_finder_index
from .retrieval.service.models import (
    HybridFusionMethod,
    RetrievalMethod,
    RetrieverConfig,
    RetrieverSearchResult,
    SearchConfig,
)
from .retrieval.service.retriever import Retriever

__all__ = [
    "BM25Index",
    "BuildConfig",
    "BuildMethod",
    "CatalogRecord",
    "DynamicTreeConfig",
    "EmbeddingClient",
    "EmbeddingIndex",
    "EmbeddingRecord",
    "EmbeddingLeafHit",
    "EmbeddingLeafRanker",
    "HybridFusionMethod",
    "IndexBuilder",
    "IndexBuildRuntimeConfig",
    "IndexedEmbeddingRecord",
    "LLMClient",
    "NodeRuntime",
    "OpenAIEmbeddingClient",
    "CIDTreeTopKRetriever",
    "CIDTreeRetrieverConfig",
    "CIDTreeRetrieverResult",
    "Orchestrator",
    "OrchestratorConfig",
    "OrchestratorResponse",
    "OrchestratorStatus",
    "RetrievalMethod",
    "Retriever",
    "RetrieverConfig",
    "RetrieverSearchResult",
    "SearchConfig",
    "TreeBuilder",
    "TreeNode",
    "build_bm25_index",
    "build_embedding_index",
    "build_leaf_embedding_text",
    "build_tree",
    "create_openai_embedding_client",
    "create_orchestrator",
    "load_bm25_index",
    "load_embedding_index",
    "load_finder_index",
]
