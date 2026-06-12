# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Thin wrapper around IndexManager.search with graceful degradation.

Returns a ranked item_id list, or None when the retrieval system is unavailable
(caller should fall back to MySQL LIKE).
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_SKILL_TYPE = "skill"
MAX_TOP_K = 500


def plugin_type_to_group(plugin_type: str) -> str:
    """Route plugin_type to index group: skill → skill, all others → plugin."""
    return "skill" if (plugin_type or "").lower() == _SKILL_TYPE else "plugin"


def retrieval_search(
    index_manager,
    plugin_type: str,
    query: str,
    page: int,
    page_size: int,
    method: str = "embedding",
) -> Optional[List[str]]:
    """Return ranked item_id list or None (triggers LIKE fallback in caller)."""
    group = plugin_type_to_group(plugin_type)
    if not index_manager.is_ready(group):
        logger.info("retrieval_search: index not ready for group=%s, fallback", group)
        return None
    top_k = min(page * page_size, MAX_TOP_K)
    try:
        result = index_manager.search(group, query, top_k, method=method)
        if result is None:
            logger.warning("retrieval_search: None result group=%s query=%r, fallback", group, query)
        return result
    except Exception as exc:
        logger.error("retrieval_search error group=%s query=%r: %s", group, query, exc)
        return None
