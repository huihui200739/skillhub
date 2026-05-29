# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Protocol, Sequence

try:
    import numpy as np
except ModuleNotFoundError:
    np = None  # type: ignore[assignment]

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None  # type: ignore[assignment]


class EmbeddingClient(Protocol):
    def embed_texts(self, texts: Sequence[str], *, batch_size: int = 64) -> List[List[float]]:
        ...

    def embed_text(self, text: str) -> List[float]:
        ...


class OpenAIEmbeddingClient:
    def __init__(self, *, client: Any, model: str) -> None:
        if client is None:
            raise ValueError("OpenAI client must be provided")
        if not str(model or "").strip():
            raise ValueError("Embedding model must be non-empty")
        self._client = client
        self.model = str(model)

    def embed_text(self, text: str) -> List[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: Sequence[str], *, batch_size: int = 64) -> List[List[float]]:
        vectors: List[List[float]] = []
        chunk_size = max(1, int(batch_size))
        for start in range(0, len(texts), chunk_size):
            chunk = [str(text) for text in texts[start: start + chunk_size]]
            vectors.extend(self._embed_chunk_with_backoff(chunk))
        return vectors

    def _embed_chunk_with_backoff(self, chunk: Sequence[str]) -> List[List[float]]:
        if not chunk:
            return []
        try:
            response = self._client.embeddings.create(model=self.model, input=list(chunk))
        except Exception as exc:
            message = str(exc).lower()
            if len(chunk) > 1 and ("batch size" in message or "should not be larger than" in message):
                midpoint = max(1, len(chunk) // 2)
                left = self._embed_chunk_with_backoff(chunk[:midpoint])
                right = self._embed_chunk_with_backoff(chunk[midpoint:])
                return left + right
            raise
        return [[float(value) for value in item.embedding] for item in response.data]


def create_openai_embedding_client(*, base_url: str, api_key: str, model: str) -> OpenAIEmbeddingClient:
    if OpenAI is None:
        raise RuntimeError("openai package is required to create an OpenAI embedding client")
    return OpenAIEmbeddingClient(client=OpenAI(base_url=base_url, api_key=api_key), model=model)


@dataclass(frozen=True)
class EmbeddingRecord:
    choice_id: str
    payload: str
    text: str
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexedEmbeddingRecord:
    choice_id: str
    payload: str
    text: str
    vector: tuple[float, ...]
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingIndex:
    model_name: str
    dimensions: int
    records: tuple[IndexedEmbeddingRecord, ...]
    faiss_index_b64: str = ""
    faiss_index_type: str = "IndexFlatIP"
    metadata: Dict[str, object] = field(default_factory=dict)


class _FaissByteBuffer:
    def __init__(self, payload: bytes) -> None:
        self._payload = bytes(payload)
        self.shape = (len(self._payload),)

    def tobytes(self) -> bytes:
        return self._payload


def _require_faiss():
    try:
        return importlib.import_module("faiss")
    except ModuleNotFoundError as exc:
        raise RuntimeError("faiss package is required for embedding index build/search") from exc


def _l2_normalize_rows(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for vector in vectors:
        values = [float(value) for value in vector]
        norm = sum(value * value for value in values) ** 0.5
        if norm > 0.0:
            values = [value / norm for value in values]
        normalized.append(values)
    return normalized


def _build_faiss_index_blob(vectors: Sequence[Sequence[float]]) -> tuple[str, int]:
    if not vectors:
        return "", 0
    faiss_module = _require_faiss()
    normalized = _l2_normalize_rows(vectors)
    dimensions = len(normalized[0])
    index = faiss_module.IndexFlatIP(dimensions)
    matrix = np.asarray(normalized, dtype="float32") if np is not None else normalized
    index.add(matrix)
    serialized = faiss_module.serialize_index(index)
    if isinstance(serialized, bytes):
        blob = serialized
    else:
        blob = bytes(serialized)
    return base64.b64encode(blob).decode("ascii"), dimensions


def deserialize_faiss_index(index: EmbeddingIndex):
    if not index.faiss_index_b64:
        return None
    faiss_module = _require_faiss()
    payload = base64.b64decode(index.faiss_index_b64.encode("ascii"))
    candidates: list[object] = []
    if np is not None:
        candidates.append(np.frombuffer(payload, dtype="uint8"))
    candidates.append(_FaissByteBuffer(payload))
    candidates.append(payload)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return faiss_module.deserialize_index(candidate)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return None


def build_embedding_index(
    *,
    records: Sequence[EmbeddingRecord],
    embedding_client: EmbeddingClient,
    model_name: str,
    batch_size: int = 64,
    metadata: Dict[str, object] | None = None,
) -> EmbeddingIndex:
    if not records:
        return EmbeddingIndex(
            model_name=str(model_name or ""),
            dimensions=0,
            records=(),
            metadata=dict(metadata or {}),
        )
    texts = [str(record.text) for record in records]
    vectors = embedding_client.embed_texts(texts, batch_size=max(1, int(batch_size)))
    if len(vectors) != len(records):
        raise ValueError(f"Embedding client returned {len(vectors)} vectors for {len(records)} records")
    indexed: List[IndexedEmbeddingRecord] = []
    dimensions = len(vectors[0]) if vectors else 0
    for record, vector in zip(records, vectors):
        if len(vector) != dimensions:
            raise ValueError("Embedding vectors must have consistent dimensions")
        indexed.append(
            IndexedEmbeddingRecord(
                choice_id=str(record.choice_id),
                payload=str(record.payload),
                text=str(record.text),
                vector=tuple(float(value) for value in vector),
                description=str(record.description),
                metadata=dict(record.metadata or {}),
            )
        )
    faiss_index_b64, faiss_dimensions = _build_faiss_index_blob(vectors)
    if faiss_dimensions != dimensions:
        raise ValueError("FAISS index dimensions must match embedding vector dimensions")
    return EmbeddingIndex(
        model_name=str(model_name or ""),
        dimensions=dimensions,
        records=tuple(indexed),
        faiss_index_b64=faiss_index_b64,
        faiss_index_type="IndexFlatIP",
        metadata=dict(metadata or {}),
    )


def build_embedding_index_from_indexed_records(
    *,
    records: Sequence[IndexedEmbeddingRecord],
    model_name: str,
    metadata: Dict[str, object] | None = None,
) -> EmbeddingIndex:
    if not records:
        return EmbeddingIndex(model_name=str(model_name or ""), dimensions=0, records=(), metadata=dict(metadata or {}))
    dimensions = len(records[0].vector)
    for record in records:
        if len(record.vector) != dimensions:
            raise ValueError("Embedding vectors must have consistent dimensions")
    try:
        faiss_index_b64, faiss_dimensions = _build_faiss_index_blob([record.vector for record in records])
    except RuntimeError:
        faiss_index_b64 = ""
        faiss_dimensions = dimensions
    if faiss_dimensions != dimensions:
        raise ValueError("FAISS index dimensions must match embedding vector dimensions")
    return EmbeddingIndex(
        model_name=str(model_name or ""),
        dimensions=dimensions,
        records=tuple(records),
        faiss_index_b64=faiss_index_b64,
        faiss_index_type="IndexFlatIP",
        metadata=dict(metadata or {}),
    )


def save_embedding_index(index: EmbeddingIndex, path: str | Path) -> None:
    output_path = Path(path)
    payload = {
        "model_name": index.model_name,
        "dimensions": index.dimensions,
        "faiss_index_b64": index.faiss_index_b64,
        "faiss_index_type": index.faiss_index_type,
        "metadata": dict(index.metadata or {}),
        "records": [
            {
                "choice_id": record.choice_id,
                "payload": record.payload,
                "text": record.text,
                "description": record.description,
                "metadata": dict(record.metadata or {}),
                "vector": list(record.vector),
            }
            for record in index.records
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_embedding_index(path: str | Path) -> EmbeddingIndex:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = [
        IndexedEmbeddingRecord(
            choice_id=str(item["choice_id"]),
            payload=str(item["payload"]),
            text=str(item["text"]),
            description=str(item.get("description") or ""),
            metadata=dict(item.get("metadata") or {}),
            vector=tuple(float(value) for value in item.get("vector") or []),
        )
        for item in payload.get("records") or []
    ]
    return EmbeddingIndex(
        model_name=str(payload.get("model_name") or ""),
        dimensions=int(payload.get("dimensions") or 0),
        records=tuple(records),
        faiss_index_b64=str(payload.get("faiss_index_b64") or ""),
        faiss_index_type=str(payload.get("faiss_index_type") or "IndexFlatIP"),
        metadata=dict(payload.get("metadata") or {}),
    )


def load_embedding_records_jsonl(path: str | Path) -> List[EmbeddingRecord]:
    records: List[EmbeddingRecord] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        records.append(
            EmbeddingRecord(
                choice_id=str(payload["choice_id"]),
                payload=str(payload["payload"]),
                text=str(payload["text"]),
                description=str(payload.get("description") or ""),
                metadata=dict(payload.get("metadata") or {}),
            )
        )
    return records


def build_embedding_index_from_jsonl(
    *,
    input_path: str | Path,
    output_path: str | Path,
    embedding_client: EmbeddingClient,
    model_name: str,
    batch_size: int = 64,
    metadata: Dict[str, object] | None = None,
) -> EmbeddingIndex:
    records = load_embedding_records_jsonl(input_path)
    index = build_embedding_index(
        records=records,
        embedding_client=embedding_client,
        model_name=model_name,
        batch_size=batch_size,
        metadata=metadata,
    )
    save_embedding_index(index, output_path)
    return index


__all__ = [
    "EmbeddingClient",
    "EmbeddingIndex",
    "EmbeddingRecord",
    "IndexedEmbeddingRecord",
    "OpenAIEmbeddingClient",
    "build_embedding_index",
    "build_embedding_index_from_indexed_records",
    "build_embedding_index_from_jsonl",
    "create_openai_embedding_client",
    "deserialize_faiss_index",
    "load_embedding_index",
    "load_embedding_records_jsonl",
    "save_embedding_index",
]
