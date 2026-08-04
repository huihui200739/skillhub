"""Milvus connection / collection helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from recommender.shared.config import load_config

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("asset_id", "category_id", "embedding")

_PYMILVUS_INSTALL_HINT = (
    "pymilvus is required when MARKET_RECOMMENDER_ENABLED=true. "
    "Install with: cd marketplace && uv sync --extra recommender"
)


@dataclass(frozen=True)
class CollectionConfig:
    host: str
    port: int
    collection: str
    dim: int
    recreate: bool


def load_collection_config(dim: int, *, recreate: bool = False) -> CollectionConfig:
    settings = load_config().milvus
    return CollectionConfig(
        host=settings.host,
        port=settings.port,
        collection=settings.collection,
        dim=dim,
        recreate=recreate,
    )


def connect_milvus(cfg: CollectionConfig) -> None:
    try:
        from pymilvus import connections
    except ImportError as exc:
        raise ImportError(_PYMILVUS_INSTALL_HINT) from exc

    connections.connect(
        alias="default",
        host=cfg.host,
        port=cfg.port,
        timeout=30,
    )


def _collection_has_required_fields(collection: Any) -> bool:
    try:
        names = {f.name for f in collection.schema.fields}
    except Exception:
        return False
    return all(name in names for name in REQUIRED_FIELDS)


def ensure_collection(cfg: CollectionConfig):
    try:
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility
    except ImportError as exc:
        raise ImportError(_PYMILVUS_INSTALL_HINT) from exc

    if cfg.recreate and utility.has_collection(cfg.collection):
        utility.drop_collection(cfg.collection)

    if utility.has_collection(cfg.collection):
        collection = Collection(cfg.collection)
        if _collection_has_required_fields(collection):
            return collection
        raise RuntimeError(
            f"Milvus collection {cfg.collection!r} is missing required fields "
            f"{REQUIRED_FIELDS}; run milvus full rebuild with recreate "
            "(MARKET_REC_REBUILD_ON_STARTUP or --mode full)."
        )

    schema = CollectionSchema(
        fields=[
            FieldSchema(
                name="asset_id",
                dtype=DataType.VARCHAR,
                is_primary=True,
                max_length=64,
            ),
            FieldSchema(
                name="category_id",
                dtype=DataType.VARCHAR,
                max_length=64,
            ),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=cfg.dim,
            ),
        ],
        description="Swarm skill embeddings",
    )
    collection = Collection(name=cfg.collection, schema=schema)
    try:
        collection.create_index(
            field_name="category_id",
            index_params={"index_type": "INVERTED"},
        )
    except Exception:
        logger.warning("failed to create category_id scalar index", exc_info=True)
    return collection


def delete_by_asset_ids(collection: Any, asset_ids: list[str]) -> int:
    if not asset_ids:
        return 0
    quoted = ", ".join(f'"{asset_id}"' for asset_id in asset_ids)
    collection.delete(f"asset_id in [{quoted}]")
    return len(asset_ids)


def create_vector_index_if_needed(collection: Any) -> None:
    try:
        if collection.indexes:
            collection.load()
            return
    except Exception:
        logger.warning(
            "failed to inspect existing milvus indexes; will create embedding index",
            exc_info=True,
        )

    collection.create_index(
        field_name="embedding",
        index_params={
            "index_type": "HNSW",
            "metric_type": "IP",
            "params": {"M": 8, "efConstruction": 64},
        },
    )
    collection.load()
