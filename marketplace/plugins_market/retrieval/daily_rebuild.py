"""Full index rebuild: queries DB for valid item_paths, calls IndexBuilder.build,
broadcasts index:reload to Redis, and GCs old index versions on OBS.

Index OBS structure (per group):
  {group_prefix}/{YYYYMMDDH}/manifest.json
  {group_prefix}/{YYYYMMDDH}/...

  skill example:  skills-index/2026042117/manifest.json
  plugin example: plugins-index/2026042117/manifest.json

group_prefix is configurable via:
  MARKET_RETRIEVAL_SKILL_INDEX_OBS_PREFIX  (default: skills-index)
  MARKET_RETRIEVAL_PLUGIN_INDEX_OBS_PREFIX (default: plugins-index)

Rollback: up to _MAX_INDEX_VERSIONS successful builds are kept on OBS.
If a build fails the in-memory index is unchanged; on restart warm-start
loads the latest existing dir (which is always the last successful build).
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SKILL_GROUP = "skill"
PLUGIN_GROUP = "plugin"
_SKILL_TYPE = "skill"
_PLUGIN_TYPES = ("tools", "mcp-stdio", "restful-api")
_MAX_INDEX_VERSIONS = 168  # fallback default; overridden at call site via settings
_RELOAD_STREAM = "index:reload"
_MATERIALIZED_ITEM_INDEX_RE = re.compile(r"item-(\d+)\.zip")
_CN_TZ = ZoneInfo("Asia/Shanghai")


def _index_dir_name() -> str:
    """Return datetime-to-second string for index dir, e.g. '20260422010203'."""
    return datetime.now(_CN_TZ).strftime("%Y%m%d%H%M%S")


def _fetch_valid_item_paths(db, group: str, bucket_name: str) -> List[str]:
    """Return OBS zip URIs for non-OFFLINE, latest-version plugins in *group*."""
    from plugins_market.models.market_assets import MarketAssetDB

    if group == SKILL_GROUP:
        type_filter = MarketAssetDB.plugin_type == _SKILL_TYPE
    else:
        type_filter = MarketAssetDB.plugin_type.in_(list(_PLUGIN_TYPES))

    rows = (
        db.query(
            MarketAssetDB.asset_id,
            MarketAssetDB.publisher_id,
            MarketAssetDB.name,
            MarketAssetDB.latest_version,
            MarketAssetDB.plugin_type,
        )
        .filter(
            MarketAssetDB.status != "OFFLINE",
            MarketAssetDB.latest_version.isnot(None),
            type_filter,
        )
        .all()
    )

    paths: List[str] = []
    for row in rows:
        root = "skills" if row.plugin_type == _SKILL_TYPE else "plugins"
        safe_name = row.name.strip().replace(" ", "-")
        key = (
            f"{root}/{row.publisher_id}/{row.asset_id}"
            f"/{row.latest_version}/{safe_name}_{row.latest_version}.zip"
        )
        paths.append(f"obs://{bucket_name}/{key}")
    return paths


def list_index_dirs(storage, group_prefix: str) -> List[str]:
    """List index dir prefixes under group_prefix on OBS, sorted newest-first.

    Returns paths like ["skills-index/2026042117", "skills-index/2026042116"].
    """
    prefix = group_prefix.rstrip("/") + "/"
    all_keys = storage.list_keys(prefix)
    dirs: set = set()
    for key in all_keys:
        rest = key[len(prefix):]
        slash = rest.find("/")
        if slash > 0:
            dirs.add(f"{group_prefix.rstrip('/')}/{rest[:slash]}")
    return sorted(dirs, reverse=True)


def _gc_old_indexes(storage, group_prefix: str, max_versions: int = _MAX_INDEX_VERSIONS) -> None:
    """Delete oldest index dirs on OBS beyond max_versions."""
    dirs = list_index_dirs(storage, group_prefix)
    for old_dir in dirs[max_versions:]:
        result = storage.delete_prefix(old_dir + "/")
        if result.get("success"):
            logger.info("GC old OBS index: %s", old_dir)
        else:
            logger.warning("GC failed %s: %s", old_dir, result.get("errors"))


def _extract_failed_item_index(exc: Exception) -> Optional[int]:
    """Parse temp materialized zip name like item-3.zip from build errors."""
    match = _MATERIALIZED_ITEM_INDEX_RE.search(str(exc))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def rebuild_one_group(
    group: str,
    db,
    group_prefix: str,
    storage,
    index_manager=None,
    redis_client=None,
    build_config=None,
    max_index_versions: int = _MAX_INDEX_VERSIONS,
) -> Optional[str]:
    """Full rebuild for one index group. Returns new OBS index URI or None on failure.

    group_prefix: OBS key prefix for this group, e.g. "skills-index" or "plugins-index".
    Output path:  obs://{bucket}/{group_prefix}/{YYYYMMDDH}/
    build_config: BuildConfig with resolved model clients / credentials.
    """
    try:
        from indexing.workflows.index_builder import IndexBuilder  # type: ignore[import]
    except ImportError:
        logger.warning("retrieval module not importable — rebuild skipped for group=%s", group)
        return None

    bucket_name = storage.config.bucket_name
    t0 = time.monotonic()
    item_paths = _fetch_valid_item_paths(db, group, bucket_name)
    logger.info("rebuild_one_group: group=%s items=%d", group, len(item_paths))

    if not item_paths:
        logger.warning("rebuild_one_group: no items for group=%s, skipping", group)
        return None

    output_dir = f"obs://{bucket_name}/{group_prefix.rstrip('/')}/{_index_dir_name()}"

    build_inputs = list(item_paths)
    while build_inputs:
        try:
            new_path = IndexBuilder.build(build_inputs, output_dir, item_type=group, config=build_config)
            break
        except Exception as exc:
            bad_index = _extract_failed_item_index(exc)
            if bad_index is None:
                logger.error("IndexBuilder.build failed group=%s: %s", group, exc, exc_info=True)
                return None
            if bad_index < 0 or bad_index >= len(build_inputs):
                logger.error(
                    "IndexBuilder.build failed group=%s with invalid bad_index=%s (inputs=%d): %s",
                    group,
                    bad_index,
                    len(build_inputs),
                    exc,
                    exc_info=True,
                )
                return None
            if len(build_inputs) == 1:
                logger.error(
                    "IndexBuilder.build failed group=%s and last candidate is invalid path=%s: %s",
                    group,
                    build_inputs[0],
                    exc,
                    exc_info=True,
                )
                return None
            bad_path = build_inputs.pop(bad_index)
            logger.error(
                "IndexBuilder.build failed group=%s due to bad package idx=%s path=%s; "
                "skip it and retry with remaining=%d",
                group,
                bad_index,
                bad_path,
                len(build_inputs),
            )

    new_path_str = str(new_path)
    elapsed = time.monotonic() - t0
    logger.info("rebuild done group=%s path=%s elapsed=%.1fs", group, new_path_str, elapsed)

    _gc_old_indexes(storage, group_prefix, max_versions=max_index_versions)

    if index_manager is not None:
        index_manager.load(group, new_path_str)

    if redis_client is not None:
        try:
            redis_client.xadd(_RELOAD_STREAM, {"group": group, "index_path": new_path_str})
        except Exception as exc:
            logger.warning("broadcast reload failed group=%s: %s", group, exc)

    return new_path_str


_REBUILD_LOCK_KEY = "retrieval:rebuild:lock"
_REBUILD_LOCK_TTL_SECONDS = 2400  # 40-minute upper bound for a full rebuild run


def rebuild_all(
    db_factory,
    skill_prefix: str,
    plugin_prefix: str,
    storage,
    index_manager=None,
    redis_client=None,
    build_config=None,
    max_index_versions: int = _MAX_INDEX_VERSIONS,
    skip_lock: bool = False,
) -> None:
    """Rebuild both index groups. Called from thread-pool by the scheduled job.

    When Redis is available a SET NX distributed lock ensures only one instance
    runs the rebuild at a time (multi-worker / multi-pod deployments). The lock
    expires automatically after _REBUILD_LOCK_TTL_SECONDS so a crashed holder
    does not block future runs.

    skip_lock=True: bypass the distributed lock entirely (used for startup rebuild
    so a stale lock from a crashed previous process does not block the new run).
    """
    lock_acquired = False
    if redis_client is not None and not skip_lock:
        lock_acquired = bool(redis_client.set(_REBUILD_LOCK_KEY, "1", nx=True, ex=_REBUILD_LOCK_TTL_SECONDS))
        if not lock_acquired:
            logger.info("rebuild_all: another instance holds the rebuild lock, skipping this run")
            return

    try:
        for group, prefix in ((SKILL_GROUP, skill_prefix), (PLUGIN_GROUP, plugin_prefix)):
            db = db_factory()
            try:
                rebuild_one_group(group, db, prefix, storage, index_manager, redis_client, build_config, max_index_versions)
            finally:
                db.close()
    finally:
        if redis_client is not None and lock_acquired:
            try:
                redis_client.delete(_REBUILD_LOCK_KEY)
            except Exception as exc:
                logger.warning("rebuild_all: failed to release rebuild lock: %s", exc)
