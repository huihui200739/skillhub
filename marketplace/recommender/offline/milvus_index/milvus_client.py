"""Milvus connection / collection helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from recommender.shared.config import _env, load_config

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("asset_id", "category_id", "embedding")


@dataclass(frozen=True)
class CollectionConfig:
    host: str
    port: int
    collection: str
    dim: int
    recreate: bool
    user: str = ""
    password: str = ""


def _resolve_milvus_password(plaintext_fallback: str = "") -> str:
    """Prefer SecurityUtils decrypt for MILVUS_PASSWORD / MARKET_MILVUS_PASSWORD."""
    try:
        from common.security.security_utils import SecurityUtils

        for key in ("MILVUS_PASSWORD", "MARKET_MILVUS_PASSWORD"):
            value = SecurityUtils.get_decrypt_secret(key, default="") or ""
            if value.strip():
                return value.strip()
    except Exception as exc:
        logger.warning(
            "decrypt MILVUS_PASSWORD failed (%s); fallback to env plaintext",
            exc,
        )
    return (plaintext_fallback or _env("MILVUS_PASSWORD", "MARKET_MILVUS_PASSWORD", default="")).strip()


def load_collection_config(dim: int, *, recreate: bool = False) -> CollectionConfig:
    settings = load_config().milvus
    return CollectionConfig(
        host=settings.host,
        port=settings.port,
        collection=settings.collection,
        dim=dim,
        recreate=recreate,
        user=(settings.user or "").strip(),
        password=_resolve_milvus_password(settings.password),
    )


def connect_milvus(cfg: CollectionConfig, *, timeout: float = 30.0) -> None:
    from pymilvus import connections

    kwargs: dict[str, Any] = {
        "alias": "default",
        "host": cfg.host,
        "port": cfg.port,
        "timeout": timeout,
    }
    user = (cfg.user or "").strip()
    password = (cfg.password or "").strip()
    if user:
        kwargs["user"] = user
        kwargs["password"] = password
    connections.connect(**kwargs)


def _collection_has_required_fields(collection: Any) -> bool:
    try:
        names = {f.name for f in collection.schema.fields}
    except Exception:
        return False
    return all(name in names for name in REQUIRED_FIELDS)


def ensure_collection(cfg: CollectionConfig):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

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


def _has_embedding_vector_index(collection: Any) -> bool:
    """True only when the FLOAT_VECTOR field already has an index (not scalar indexes)."""
    try:
        indexes = list(collection.indexes or [])
    except Exception:
        return False
    for idx in indexes:
        field = getattr(idx, "field_name", None)
        if field is None:
            # Older / alternate pymilvus shapes.
            field = getattr(idx, "field", None)
        if str(field or "") == "embedding":
            return True
    return False


def create_vector_index_if_needed(collection: Any) -> None:
    # ensure_collection may already create a category_id scalar index. Do NOT treat
    # "any index exists" as enough for load(): Milvus requires a vector index on
    # embedding before Collection.load().
    try:
        if _has_embedding_vector_index(collection):
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
