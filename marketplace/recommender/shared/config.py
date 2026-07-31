"""Recommender configuration loaded from env (MARKET_* / bare aliases)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # marketplace/
DATA_ROOT = ROOT.parent / "data" / "skill_packages"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return default


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    name: str


@dataclass(frozen=True)
class StorageConfig:
    bucket: str
    endpoint: str
    access_key: str
    secret_key: str
    region: str
    use_ssl: bool
    addressing_style: str


@dataclass(frozen=True)
class TopKInstallSettings:
    key: str
    k: int
    ttl_seconds: int
    interval_minutes: int


@dataclass(frozen=True)
class UserSeqSettings:
    key_prefix: str
    max_len: int
    ttl_seconds: int


@dataclass(frozen=True)
class RedisConfig:
    host: str
    port: int
    db: int
    password: str
    ssl: bool
    topk_install: TopKInstallSettings
    user_seq: UserSeqSettings


@dataclass(frozen=True)
class MilvusSettings:
    host: str
    port: int
    collection: str
    batch_size: int
    incremental_interval_minutes: int
    full_rebuild_hour: int


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    storage: StorageConfig
    redis: RedisConfig
    milvus: MilvusSettings
    download_dir: Path
    plugin_types: tuple[str, ...] | None
    sync_interval_minutes: int

    @property
    def interval_minutes(self) -> int:
        return self.sync_interval_minutes


def load_config() -> AppConfig:
    plugin_raw = _env("OFFLINE_SYNC_PLUGIN_TYPES", "MARKET_REC_PLUGIN_TYPES", default="")
    plugin_types = (
        tuple(p.strip() for p in plugin_raw.split(",") if p.strip()) if plugin_raw else None
    )

    download_raw = _env(
        "OFFLINE_SYNC_DOWNLOAD_DIR",
        "MARKET_REC_DOWNLOAD_DIR",
        default=str(DATA_ROOT),
    )
    download_dir = Path(download_raw)
    if not download_dir.is_absolute():
        download_dir = ROOT / download_dir

    return AppConfig(
        database=DatabaseConfig(
            host=_env("DB_HOST", "MARKET_DB_HOST", default="localhost"),
            port=int(_env("DB_PORT", "MARKET_DB_PORT", default="3306")),
            user=_env("DB_USER", "MARKET_DB_USER", default="root"),
            password=_env("DB_PASSWORD", "MARKET_DB_PASSWORD", default=""),
            name=_env("STORE_DB_NAME", "MARKET_STORE_DB_NAME", default="openjiuwen_market"),
        ),
        storage=StorageConfig(
            bucket=_env("MARKET_BUCKET_NAME", default="test"),
            endpoint=_env("MARKET_S3_ENDPOINT", default="http://localhost:9000"),
            access_key=_env("MARKET_S3_ACCESS_KEY", default=""),
            secret_key=_env("MARKET_S3_SECRET_KEY", default=""),
            region=_env("MARKET_S3_REGION", default="") or "",
            use_ssl=_bool(os.getenv("MARKET_S3_USE_SSL"), default=False),
            addressing_style=_env("MARKET_S3_ADDRESSING_STYLE", default="path") or "path",
        ),
        redis=RedisConfig(
            host=_env("REDIS_HOST", "MARKET_REDIS_HOST", default="127.0.0.1"),
            port=int(_env("REDIS_PORT", "MARKET_REDIS_PORT", default="6379")),
            db=int(_env("REDIS_DB", "MARKET_REDIS_DB", default="0")),
            password=_env("MARKET_REDIS_PASSWORD", default="") or "",
            ssl=_bool(os.getenv("REDIS_SSL"), default=False),
            topk_install=TopKInstallSettings(
                key=_env("REDIS_TOPK_INSTALL_KEY", "MARKET_REDIS_TOPK_INSTALL_KEY", default="skill_rec:topk:install"),
                # 0 = full ranked catalog (兜底「全部」); >0 = classic TopK
                k=int(_env("REDIS_TOPK_K", "MARKET_REDIS_TOPK_K", default="0")),
                ttl_seconds=int(_env("REDIS_TOPK_TTL_SECONDS", "MARKET_REDIS_TOPK_TTL_SECONDS", default="7200")),
                interval_minutes=int(
                    _env("REDIS_TOPK_INTERVAL_MINUTES", "MARKET_REDIS_TOPK_INTERVAL_MINUTES", default="60")
                ),
            ),
            user_seq=UserSeqSettings(
                key_prefix=_env(
                    "REDIS_USER_SEQ_KEY_PREFIX",
                    "MARKET_REDIS_USER_SEQ_KEY_PREFIX",
                    default="skill_rec:user",
                ),
                max_len=int(_env("REDIS_USER_SEQ_MAX_LEN", "MARKET_REDIS_USER_SEQ_MAX_LEN", default="200")),
                ttl_seconds=int(
                    _env("REDIS_USER_SEQ_TTL_SECONDS", "MARKET_REDIS_USER_SEQ_TTL_SECONDS", default="7200")
                ),
            ),
        ),
        milvus=MilvusSettings(
            host=_env("MILVUS_HOST", "MARKET_MILVUS_HOST", default="127.0.0.1"),
            port=int(_env("MILVUS_PORT", "MARKET_MILVUS_PORT", default="19530")),
            collection=_env("MILVUS_COLLECTION", "MARKET_MILVUS_COLLECTION", default="skill_index"),
            batch_size=int(_env("MILVUS_BATCH_SIZE", "MARKET_MILVUS_BATCH_SIZE", default="32")),
            incremental_interval_minutes=int(
                _env(
                    "MILVUS_INCREMENTAL_INTERVAL_MINUTES",
                    "MARKET_MILVUS_INCREMENTAL_INTERVAL_MINUTES",
                    default="60",
                )
            ),
            full_rebuild_hour=int(
                _env("MILVUS_FULL_REBUILD_HOUR", "MARKET_MILVUS_FULL_REBUILD_HOUR", default="3")
            ),
        ),
        download_dir=download_dir,
        plugin_types=plugin_types,
        sync_interval_minutes=int(
            _env("OFFLINE_SYNC_INTERVAL_MINUTES", "MARKET_REC_PACKAGE_SYNC_INTERVAL_MINUTES", default="60")
        ),
    )


OfflineSyncConfig = AppConfig


def load_redis_config() -> RedisConfig:
    return load_config().redis
