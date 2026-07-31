"""Milvus vector fetch / search helpers."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from recommender.offline.milvus_index.milvus_client import (
    connect_milvus,
    create_vector_index_if_needed,
    ensure_collection,
    load_collection_config,
)
from recommender.online.types import RecommendItem
from recommender.shared.config import load_config

logger = logging.getLogger(__name__)

_SEARCH_PARAMS = {"metric_type": "IP", "params": {"ef": 64}}


def get_loaded_collection(*, dim: int = 1024):
    cfg = load_collection_config(dim=dim, recreate=False)
    connect_milvus(cfg)
    collection = ensure_collection(cfg)
    create_vector_index_if_needed(collection)
    return collection


def fetch_embeddings_by_ids(collection: Any, asset_ids: list[str]) -> dict[str, list[float]]:
    ids = [str(x).strip() for x in asset_ids if str(x).strip()]
    if not ids:
        return {}

    out: dict[str, list[float]] = {}
    batch_size = 64
    for i in range(0, len(ids), batch_size):
        batch = ids[i : i + batch_size]
        quoted = ", ".join(f'"{aid}"' for aid in batch)
        rows = collection.query(
            expr=f"asset_id in [{quoted}]",
            output_fields=["asset_id", "embedding"],
        )
        for row in rows:
            aid = str(row["asset_id"])
            emb = row.get("embedding")
            if emb is None:
                continue
            out[aid] = list(emb)
    return out


def search_vectors(
    collection: Any,
    vectors: np.ndarray | list[list[float]],
    *,
    top_k: int,
) -> list[list[tuple[str, float]]]:
    if isinstance(vectors, np.ndarray):
        data = vectors.astype(np.float32).tolist()
    else:
        data = vectors
    if not data:
        return []

    limit = max(1, int(top_k))
    results = collection.search(
        data=data,
        anns_field="embedding",
        param=_SEARCH_PARAMS,
        limit=limit,
        output_fields=["asset_id"],
    )
    parsed: list[list[tuple[str, float]]] = []
    for hits in results:
        row: list[tuple[str, float]] = []
        for hit in hits:
            aid = hit.entity.get("asset_id") if hasattr(hit, "entity") else None
            if not aid:
                aid = getattr(hit, "id", None)
            if not aid:
                continue
            row.append((str(aid), float(hit.distance)))
        parsed.append(row)
    return parsed


def merge_max_score(
    hits_per_query: list[list[tuple[str, float]]],
    *,
    exclude_ids: set[str] | None = None,
    top_k: int,
) -> list[RecommendItem]:
    exclude = exclude_ids or set()
    best: dict[str, float] = {}
    for hits in hits_per_query:
        for asset_id, score in hits:
            if asset_id in exclude:
                continue
            prev = best.get(asset_id)
            if prev is None or score > prev:
                best[asset_id] = score

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[: max(0, int(top_k))]
    return [RecommendItem(asset_id=aid, score=score) for aid, score in ranked]


def default_milvus_host_port() -> tuple[str, int]:
    m = load_config().milvus
    return m.host, m.port
