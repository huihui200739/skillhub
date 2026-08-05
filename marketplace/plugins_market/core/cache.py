# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""通用 Redis 缓存客户端；复用 settings 里已有的 Redis 配置。无 Redis 时静默降级（不缓存）。"""

from __future__ import annotations

from typing import Optional

from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

_client: Optional[object] = None
_init_attempted = False


def _make_client():
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    host = (settings.redis_host or "").strip()
    if not host:
        return None
    try:
        import redis  # type: ignore[import-not-found]

        from plugins_market.core.redis_client import redis_connection_kwargs, resolve_redis_ssl

        kwargs = redis_connection_kwargs(decode_responses=True)
        r = redis.Redis(**kwargs)
        r.ping()
        _client = r
        logger.info(
            "Cache: Redis OK host=%s port=%s db=%s ssl=%s backend=%s",
            host,
            settings.redis_port,
            settings.redis_db,
            resolve_redis_ssl(),
            settings.cache_backend,
        )
    except Exception as e:
        logger.warning("Cache: Redis init failed (%s), caching disabled", e)
    return _client


def cache_get(key: str) -> str | None:
    r = _make_client()
    if r is None:
        return None
    try:
        v = r.get(key)  # type: ignore[union-attr]
        return v if isinstance(v, str) else None
    except Exception as e:
        logger.debug("cache_get failed key=%s: %s", key, e)
        return None


def cache_set(key: str, value: str, ttl: int = 86400 * 7) -> None:
    r = _make_client()
    if r is None:
        return
    try:
        r.setex(key, max(1, ttl), value)  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("cache_set failed key=%s: %s", key, e)
