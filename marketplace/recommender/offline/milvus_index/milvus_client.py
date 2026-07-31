"""Milvus connection / collection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from recommender.shared.config import load_config


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
    from pymilvus import connections

    connections.connect(
        alias="default",
        host=cfg.host,
        port=cfg.port,
        timeout=30,
    )


def ensure_collection(cfg: CollectionConfig):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

    if cfg.recreate and utility.has_collection(cfg.collection):
        utility.drop_collection(cfg.collection)

    if utility.has_collection(cfg.collection):
        return Collection(cfg.collection)

    schema = CollectionSchema(
        fields=[
            FieldSchema(
                name="asset_id",
                dtype=DataType.VARCHAR,
                is_primary=True,
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
    return Collection(name=cfg.collection, schema=schema)


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
        pass

    collection.create_index(
        field_name="embedding",
        index_params={
            "index_type": "HNSW",
            "metric_type": "IP",
            "params": {"M": 8, "efConstruction": 64},
        },
    )
    collection.load()
