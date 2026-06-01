# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

from shared.limits import MAX_JSON_ARTIFACT_BYTES, read_text_file

from ..catalog.retrieval_text import BM25Document, BM25FinderConfig, lexical_tokenize


@dataclass(frozen=True)
class IndexedBM25Document:
    choice_id: str
    payload: str
    text: str
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)
    tokens: tuple[str, ...] = ()
    term_freqs: Dict[str, int] = field(default_factory=dict)
    doc_length: int = 0


@dataclass(frozen=True)
class BM25Index:
    documents: tuple[IndexedBM25Document, ...]
    avg_doc_length: float
    doc_freqs: Dict[str, int] = field(default_factory=dict)
    config: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, object] = field(default_factory=dict)


def build_bm25_index(
    *,
    documents: Sequence[BM25Document],
    config: BM25FinderConfig | None = None,
    metadata: Dict[str, object] | None = None,
) -> BM25Index:
    resolved_config = config or BM25FinderConfig()
    indexed_documents: List[IndexedBM25Document] = []
    doc_freqs: Counter[str] = Counter()
    total_length = 0
    for document in documents:
        tokens = tuple(lexical_tokenize(document.text))
        term_freqs_counter = Counter(tokens)
        doc_length = sum(term_freqs_counter.values())
        total_length += doc_length
        doc_freqs.update(term_freqs_counter.keys())
        indexed_documents.append(
            IndexedBM25Document(
                choice_id=str(document.choice_id),
                payload=str(document.payload),
                text=str(document.text),
                description=str(document.description),
                metadata=dict(document.metadata or {}),
                tokens=tokens,
                term_freqs={term: int(freq) for term, freq in term_freqs_counter.items()},
                doc_length=doc_length,
            )
        )
    avg_doc_length = total_length / max(1, len(indexed_documents)) if indexed_documents else 0.0
    return BM25Index(
        documents=tuple(indexed_documents),
        avg_doc_length=float(avg_doc_length),
        doc_freqs={term: int(freq) for term, freq in doc_freqs.items()},
        config={
            "top_k": float(resolved_config.top_k),
            "k1": float(resolved_config.k1),
            "b": float(resolved_config.b),
            "delta": float(resolved_config.delta),
        },
        metadata=dict(metadata or {}),
    )


def build_bm25_index_from_indexed_documents(
    *,
    documents: Sequence[IndexedBM25Document],
    config: BM25FinderConfig | None = None,
    metadata: Dict[str, object] | None = None,
) -> BM25Index:
    resolved_config = config or BM25FinderConfig()
    doc_freqs: Counter[str] = Counter()
    total_length = 0
    normalized_documents: List[IndexedBM25Document] = []
    for document in documents:
        term_freqs = Counter({str(term): int(freq) for term, freq in dict(document.term_freqs or {}).items()})
        tokens = tuple(document.tokens or tuple(term for term, freq in term_freqs.items() for _ in range(freq)))
        doc_length = int(document.doc_length or sum(term_freqs.values()))
        total_length += doc_length
        doc_freqs.update(term_freqs.keys())
        normalized_documents.append(
            IndexedBM25Document(
                choice_id=str(document.choice_id),
                payload=str(document.payload),
                text=str(document.text),
                description=str(document.description),
                metadata=dict(document.metadata or {}),
                tokens=tokens,
                term_freqs={term: int(freq) for term, freq in term_freqs.items()},
                doc_length=doc_length,
            )
        )
    avg_doc_length = total_length / max(1, len(normalized_documents)) if normalized_documents else 0.0
    return BM25Index(
        documents=tuple(normalized_documents),
        avg_doc_length=float(avg_doc_length),
        doc_freqs={term: int(freq) for term, freq in doc_freqs.items()},
        config={
            "top_k": float(resolved_config.top_k),
            "k1": float(resolved_config.k1),
            "b": float(resolved_config.b),
            "delta": float(resolved_config.delta),
        },
        metadata=dict(metadata or {}),
    )


def save_bm25_index(index: BM25Index, path: str | Path) -> None:
    output_path = Path(path)
    payload = {
        "documents": [
            {
                "choice_id": document.choice_id,
                "payload": document.payload,
                "text": document.text,
                "description": document.description,
                "metadata": dict(document.metadata or {}),
                "tokens": list(document.tokens),
                "term_freqs": dict(document.term_freqs or {}),
                "doc_length": int(document.doc_length),
            }
            for document in index.documents
        ],
        "avg_doc_length": float(index.avg_doc_length),
        "doc_freqs": dict(index.doc_freqs or {}),
        "config": dict(index.config or {}),
        "metadata": dict(index.metadata or {}),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_bm25_index(path: str | Path) -> BM25Index:
    payload = json.loads(read_text_file(path, max_bytes=MAX_JSON_ARTIFACT_BYTES, label="BM25 index"))
    documents = [
        IndexedBM25Document(
            choice_id=str(item.get("choice_id") or ""),
            payload=str(item.get("payload") or ""),
            text=str(item.get("text") or ""),
            description=str(item.get("description") or ""),
            metadata=dict(item.get("metadata") or {}),
            tokens=tuple(str(token) for token in item.get("tokens") or ()),
            term_freqs={str(term): int(freq) for term, freq in dict(item.get("term_freqs") or {}).items()},
            doc_length=int(item.get("doc_length") or 0),
        )
        for item in payload.get("documents") or ()
    ]
    return BM25Index(
        documents=tuple(documents),
        avg_doc_length=float(payload.get("avg_doc_length") or 0.0),
        doc_freqs={str(term): int(freq) for term, freq in dict(payload.get("doc_freqs") or {}).items()},
        config={str(key): float(value) for key, value in dict(payload.get("config") or {}).items()},
        metadata=dict(payload.get("metadata") or {}),
    )


__all__ = [
    "BM25Index",
    "IndexedBM25Document",
    "build_bm25_index",
    "build_bm25_index_from_indexed_documents",
    "load_bm25_index",
    "save_bm25_index",
]
