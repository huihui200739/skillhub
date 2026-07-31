"""Embedding helpers for skill texts."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-m3"
_HF_CACHE_MARKER = Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-m3"


def make_embedding_model() -> SentenceTransformer:
    # Official huggingface.co is often unreachable in CN; mirror works for hub downloads.
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if _HF_CACHE_MARKER.is_dir():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        logger.info("Using cached embedding model offline: %s", _HF_CACHE_MARKER)
    else:
        # First download: clear any prior offline flags (old pipeline forced HF_HUB_OFFLINE=1).
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        logger.info(
            "Loading embedding model %s (HF_ENDPOINT=%s)",
            MODEL_NAME,
            os.environ.get("HF_ENDPOINT"),
        )
    return SentenceTransformer(MODEL_NAME)


def embed_texts(model: SentenceTransformer, texts: list[str]) -> np.ndarray:
    emb = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(emb, dtype=np.float32)


def upsert_batch(collection, asset_ids: list[str], vectors: np.ndarray) -> int:
    vectors_list = vectors.tolist()
    try:
        collection.upsert([asset_ids, vectors_list])
        return len(asset_ids)
    except Exception as exc:
        logger.warning("upsert failed, fallback to insert: %s", exc)
        collection.insert([asset_ids, vectors_list])
        return len(asset_ids)
