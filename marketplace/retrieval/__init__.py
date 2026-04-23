"""Public SDK entrypoints for the repository.

This module re-exports canonical package APIs only. It must not depend on
demo/data/tests/training/scripts repository directories.
"""

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
from .orchestration.engine.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    OrchestratorResponse,
    OrchestratorStatus,
    create_orchestrator,
)
from .orchestration.llm.client import LLMClient
from .orchestration.retrieval_adapter import (
    CIDTreeTopKRetriever,
    EmbeddingLeafHit,
    EmbeddingLeafRanker,
    RetrieverConfig as CIDTreeRetrieverConfig,
    RetrieverResult as CIDTreeRetrieverResult,
    build_leaf_embedding_text,
)
from .orchestration.runtime import NodeRuntime
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
