"""Redis connection helpers (generic; not tied to any one snapshot)."""

from __future__ import annotations

from recommender.shared.config import RedisConfig


def create_redis_client(cfg: RedisConfig):
    import redis

    return redis.Redis(
        host=cfg.host,
        port=cfg.port,
        db=cfg.db,
        password=cfg.password or None,
        ssl=cfg.ssl,
        decode_responses=True,
    )
