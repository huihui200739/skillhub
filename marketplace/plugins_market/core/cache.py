# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""通用 Redis 缓存客户端；复用 settings 里已有的 Redis 配置。无 Redis 时静默降级（不缓存）。"""

from __future__ import annotations

from typing import Optional

from common.security.security_utils import SecurityUtils
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
        password = (SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="") or "").strip()
        kwargs: dict = {
            "host": host,
            "port": int(settings.redis_port),
            "db": int(settings.redis_db),
            "decode_responses": True,
            # 空闲连接被静默丢弃时限时报错，由 cache_get/set 兜底降级，不挂住请求线程
            "socket_connect_timeout": 2.0,
            "socket_timeout": 2.0,
            "health_check_interval": 30,
        }
        if password:
            kwargs["password"] = password
        r = redis.Redis(**kwargs)
        r.ping()
        _client = r
        logger.info("Cache: Redis OK host=%s port=%s db=%s", host, settings.redis_port, settings.redis_db)
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


def cache_incr(key: str, ttl: int | None = None) -> int | None:
    """原子自增计数器（Redis INCR）。无 Redis 或出错时返回 None，不影响业务。

    Args:
        key: 计数器 key
        ttl: 首次创建时设置的过期时间（秒）；None = 永不过期。已存在的 key 不重设 TTL。
    Returns:
        自增后的值，或 None（Redis 不可用）
    """
    r = _make_client()
    if r is None:
        return None
    try:
        new_val = r.incr(key)  # type: ignore[union-attr]
        if ttl is not None and new_val == 1:
            r.expire(key, max(1, ttl))  # type: ignore[union-attr]
        return new_val
    except Exception as e:
        logger.debug("cache_incr failed key=%s: %s", key, e)
        return None
