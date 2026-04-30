# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Canonical offline indexing package."""

from .bm25.index import BM25Index, IndexedBM25Document, build_bm25_index
from .bm25.io import load_bm25_index, save_bm25_index
from .catalog.records import CatalogRecord
from .catalog.retrieval_text import build_embedding_record_text
from .embedding.index import (
    EmbeddingClient,
    EmbeddingIndex,
    EmbeddingRecord,
    IndexedEmbeddingRecord,
    OpenAIEmbeddingClient,
    build_embedding_index,
    build_embedding_index_from_jsonl,
    create_openai_embedding_client,
    deserialize_faiss_index,
)
from .embedding.io import load_embedding_index, load_embedding_records_jsonl, save_embedding_index
from .models import (
    BM25_INDEX_FILENAME,
    CATALOG_FILENAME,
    EMBEDDING_INDEX_FILENAME,
    EMBEDDING_RECORDS_FILENAME,
    INDEX_MANIFEST_FILENAME,
    TREE_HTML_FILENAME,
    TREE_INDEX_FILENAME,
)

__all__ = [
    "BM25Index",
    "BM25_INDEX_FILENAME",
    "CATALOG_FILENAME",
    "CatalogRecord",
    "EMBEDDING_INDEX_FILENAME",
    "EMBEDDING_RECORDS_FILENAME",
    "EmbeddingClient",
    "EmbeddingIndex",
    "EmbeddingRecord",
    "INDEX_MANIFEST_FILENAME",
    "IndexedBM25Document",
    "IndexedEmbeddingRecord",
    "OpenAIEmbeddingClient",
    "TREE_HTML_FILENAME",
    "TREE_INDEX_FILENAME",
    "build_bm25_index",
    "build_embedding_index",
    "build_embedding_index_from_jsonl",
    "build_embedding_record_text",
    "create_openai_embedding_client",
    "deserialize_faiss_index",
    "load_bm25_index",
    "load_embedding_index",
    "load_embedding_records_jsonl",
    "save_bm25_index",
    "save_embedding_index",
]
