# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""IndexManager: RCU (Read-Copy-Update) index reference holder.

Concurrent reads are lock-free; index swap holds a brief write lock.
One singleton instance is shared across the process via get_index_manager().
"""

import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

from plugins_market.core.config import settings

logger = logging.getLogger(__name__)

SKILL_GROUP = "skill"
PLUGIN_GROUP = "plugin"

# Storage path pattern: obs:// (OBS) or s3:// (MinIO) prefix
_OBS_ASSET_ID_RE = re.compile(r'^(?:obs|s3)://[^/]+/(?:skills|plugins)/([^/]+)/([^/]+)/')


def _build_cid_to_asset_map(retriever) -> Dict[str, str]:
    """Extract CID → asset_id mapping from catalog record metadata.

    Each catalog record carries metadata["skill_path"] = the original OBS URI,
    which embeds the asset_id directly. This is reliable for both skills and
    plugins regardless of whether the zip directory name matches the plugin name.
    """
    try:
        loaded_index = getattr(retriever, "_loaded_index", None)
        if loaded_index is None:
            return {}
        catalog_records = getattr(loaded_index, "catalog_records", ()) or ()
        cid_map: Dict[str, str] = {}
        for record in catalog_records:
            cid = getattr(record, "payload", "") or ""
            if not cid:
                continue
            metadata = getattr(record, "metadata", {}) or {}
            skill_path = metadata.get("skill_path", "") or ""
            if not skill_path:
                continue
            m = _OBS_ASSET_ID_RE.match(str(skill_path))
            if not m:
                continue
            cid_map[cid] = m.group(2)
        return cid_map
    except Exception as exc:
        logger.warning("_build_cid_to_asset_map failed: %s", exc)
        return {}


class IndexManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._retrievers: Dict[str, object] = {}
        self._cid_maps: Dict[str, Dict[str, str]] = {}
        self._llm_client = None
        self._llm_model: str = ""
        self._embedding_client = None
        self._embedding_model: str = ""

    def configure(
        self,
        *,
        llm_openai_client=None,
        llm_model: str = "",
        embedding_openai_client=None,
        embedding_model: str = "",
    ) -> None:
        """Store model clients used when loading a Retriever. Call once at startup."""
        with self._lock:
            self._llm_client = llm_openai_client
            self._llm_model = llm_model
            self._embedding_client = embedding_openai_client
            self._embedding_model = embedding_model

    def load(self, group: str, index_dir: str | Path) -> None:
        """Load (or hot-reload) a Retriever for *group*. Atomic reference swap."""
        try:
            from retrieval.service.retriever import Retriever  # type: ignore[import]
        except ImportError:
            logger.warning("retrieval module not importable — IndexManager.load is a no-op")
            return
        with self._lock:
            llm_client = self._llm_client
            llm_model = self._llm_model
            embedding_client = self._embedding_client
            embedding_model = self._embedding_model
        try:
            logger.debug("IndexManager.load: starting load group=%s from %s", group, index_dir)
            new_retriever = Retriever.from_index(
                index_dir,
                llm_openai_client=llm_client,
                llm_model=llm_model,
                embedding_openai_client=embedding_client,
                embedding_model=embedding_model,
            )
            logger.debug("IndexManager.load: Retriever.from_index completed, building cid_map")
            cid_map = _build_cid_to_asset_map(new_retriever)
            logger.debug("IndexManager.load: cid_map built with %d entries", len(cid_map))
            with self._lock:
                self._retrievers[group] = new_retriever
                self._cid_maps[group] = cid_map
            logger.debug(
                "IndexManager: loaded group=%s from %s, cid_map size=%d",
                group, index_dir, len(cid_map),
            )
        except Exception as exc:
            logger.error("IndexManager.load failed group=%s path=%s: %s", group, index_dir, exc, exc_info=True)

    def search(self, group: str, query: str, top_k: int, method: str = "embedding") -> Optional[List[str]]:
        """Search index for *group*. Returns ranked asset_id list, or None on failure."""
        with self._lock:
            retriever = self._retrievers.get(group)
            cid_map = self._cid_maps.get(group, {})
        if retriever is None:
            return None
        try:
            from retrieval.service.models import RetrievalMethod, SearchConfig  # type: ignore[import]
            try:
                resolved_method = RetrievalMethod(method)
            except ValueError:
                logger.warning("IndexManager.search: unknown method=%r, falling back to embedding", method)
                resolved_method = RetrievalMethod.EMBEDDING
            cids: List[str] = retriever.search(
                query,
                config=SearchConfig(
                    top_k=top_k,
                    method=resolved_method,
                    embedding_relative_min_score=settings.retrieval_embedding_relative_min_score,
                    bm25_min_query_term_matches=settings.retrieval_bm25_min_query_term_matches,
                ),
            )
            if not cids:
                return []
            seen: set = set()
            asset_ids: List[str] = []
            for cid in cids:
                aid = cid_map.get(cid)
                if aid and aid not in seen:
                    seen.add(aid)
                    asset_ids.append(aid)
            if not asset_ids:
                logger.debug("IndexManager.search: %d CIDs but 0 mapped to asset_ids (group=%s)", len(cids), group)
            return asset_ids
        except Exception as exc:
            logger.error("IndexManager.search error group=%s: %s", group, exc)
            return None

    def is_ready(self, group: str) -> bool:
        return group in self._retrievers


_index_manager = IndexManager()


def get_index_manager() -> IndexManager:
    return _index_manager
