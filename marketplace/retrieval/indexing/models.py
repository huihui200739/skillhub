# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


INDEX_MANIFEST_FILENAME = "manifest.json"
TREE_INDEX_FILENAME = "tree_index.yaml"
TREE_HTML_FILENAME = "tree_index.html"
CATALOG_FILENAME = "catalog.jsonl"
EMBEDDING_RECORDS_FILENAME = "embedding_records.jsonl"
EMBEDDING_INDEX_FILENAME = "embedding_index.json"
BM25_INDEX_FILENAME = "bm25_index.json"


@dataclass(frozen=True)
class CatalogRecord:
    skill_id: str
    worker_id: str
    cid: str
    name: str
    description: str
    skill_path: str
    branch_path: tuple[str, ...]
    category: str
    retrieval_text: str
    metadata: Dict[str, object]
