# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从 Git 仓库批量接入 Skill：复用 skill-import 归一化与 publish，字母序处理条目。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
import time
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.errors import PublishError
from plugins_market.core.publish_result import PUBLISH_RESULT_REVIEWING
from plugins_market.core.database import SessionLocal
from plugins_market.core.s3_storage_client import S3StorageClient, get_storage_client
from plugins_market.imports.skill_entries import (
    entry_to_publish_zip,
    is_simple_skill_entry,
    is_standard_skill_entry,
    resolve_skill_entry_declared_semver,
)
from plugins_market.imports.skill_import_service import skill_import_from_staging_dir
from plugins_market.models.git_sources import GitSourceDB
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.repositories.git_source_repository import GitSourceRepository
from plugins_market.repositories.market_assets_repository import MarketAssetRepository
from plugins_market.schemas.plugin import (
    GitSyncRunResponse,
    SkillImportItemResult,
    SkillImportResponse,
    SkillImportSummary,
)
from plugins_market.services.skill_review import schedule_skill_publish_review
from plugins_market.utils.git_ref_rules import assert_git_ref_branch_or_tag
from plugins_market.utils.git_skills_subpath_rules import assert_git_skills_subpath
from plugins_market.utils.git_repo_canonical import (
    normalize_git_repo_global_key,
    parse_git_repo_owner,
)
from plugins_market.utils.git_source_dedup import compute_git_source_dedup_key
from plugins_market.utils.git_url_safety import (
    assert_public_git_clone_url,
    assert_resolved_git_host_allowed,
)
from plugins_market.validation.constants import MAX_ZIP_ENTRIES

logger = logging.getLogger(__name__)

_SIMPLE_AUTHOR_FALLBACK = "skillhub_user"
# 后台同步心跳：刷新 update_time_ms，供僵死判定；长任务导入期间须周期性 touch
_GIT_SYNC_HEARTBEAT_INTERVAL_S = 5 * 60
# 超过该时长无心跳（update_time_ms 未刷新）且仍为 syncing → 僵死
_STALE_GIT_SYNC_MS = 30 * 60 * 1000
# 首次同步从未完成（last_indexed_at_ms 为空）且无心跳超过该时长 → 列表侧可修复（偏短，依赖心跳）
_LIST_ORPHAN_NO_HEARTBEAT_MS = 15 * 60 * 1000
_STALE_SYNC_RECOVER_MESSAGE = (
    "同步未完成（后台任务可能已中断，或环境缺少 git / 无法访问仓库）。请点「再次同步」重试。"
)
_ORPHAN_SYNC_RECOVER_MESSAGE = (
    "同步可能已中断（如服务重启或长时间无进度）。请点「再次同步」重试。"
)

_local_git_sync_active: set[str] = set()
_local_git_sync_lock = threading.Lock()


def commit_sha_to_version(full_sha: str) -> str:
    """无 SKILL.md 声明版本时，使用 commit 7 位短码作为市场版本号（与 DB version 列一致）。"""
    from ..validation.constants import commit_full_sha_to_version

    return commit_full_sha_to_version(full_sha)


def _resolve_git_entry_version(entry: Path, commit_sha: str) -> str:
    v = resolve_skill_entry_declared_semver(entry)
    if v:
        return v
    return commit_sha_to_version(commit_sha)


def _assert_public_git_http_url(repo_url: str) -> str:
    return assert_public_git_clone_url(repo_url)


def is_reclaimable_git_source(db: Session, source: GitSourceDB) -> bool:
    """无关联 Skill 且未成功接入的 Git 源可由同一属主复用（释放 dedup 槽位供再次同步，不删除记录）。"""
    status = (source.last_index_status or "").strip().lower()
    if status == "syncing":
        return is_stale_git_source_sync(source) and GitSourceRepository(db).count_linked_assets(source.id) == 0
    if status in ("success", "partial_failure"):
        return False
    return GitSourceRepository(db).count_linked_assets(source.id) == 0


def mark_git_source_syncing(db: Session, source: GitSourceDB) -> None:
    """进入 syncing；保留 last_indexed_at_ms，避免再次同步被误判为孤儿。"""
    now_ms = int(time.time() * 1000)
    source.last_index_status = "syncing"
    source.last_index_error = None
    source.update_time_ms = now_ms
    db.add(source)
    db.commit()


def touch_git_source_sync_heartbeat(db: Session, source_id: str) -> None:
    """同步进行中刷新 update_time_ms，避免长耗时任务被僵死回收误判。"""
    sid = (source_id or "").strip()
    if not sid:
        return
    try:
        row = GitSourceRepository(db).get_by_id(sid)
        if row is None or (row.last_index_status or "").strip().lower() != "syncing":
            return
        row.update_time_ms = int(time.time() * 1000)
        db.add(row)
        db.commit()
    except SQLAlchemyError:
        db.rollback()


class _GitSyncHeartbeat:
    """按间隔 touch DB，供 run_git_source_sync 长任务使用。"""

    def __init__(self, db: Session, source_id: str) -> None:
        self._db = db
        self._source_id = source_id
        self._last_mono = 0.0
        self.touch()

    def touch(self) -> None:
        touch_git_source_sync_heartbeat(self._db, self._source_id)
        self._last_mono = time.monotonic()

    def maybe_touch(self) -> None:
        if time.monotonic() - self._last_mono >= _GIT_SYNC_HEARTBEAT_INTERVAL_S:
            self.touch()


def _finalize_git_source_sync_status(
    db: Session,
    source_id: str,
    *,
    status: str,
    error: str | None,
) -> None:
    """在可能污染 Session 的异常之后，用独立提交写回 Git 源同步状态。"""
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
    row = GitSourceRepository(db).get_by_id(source_id)
    if row is None:
        return
    now_ms = int(time.time() * 1000)
    row.last_index_status = status
    row.last_index_error = (error or None)[:2000] if error else None
    row.last_indexed_at_ms = now_ms
    row.update_time_ms = now_ms
    db.add(row)
    db.commit()


def is_stale_git_source_sync(source: GitSourceDB) -> bool:
    """syncing 且超过 _STALE_GIT_SYNC_MS 未刷新 update_time_ms（无心跳）。"""
    if (source.last_index_status or "").strip().lower() != "syncing":
        return False
    updated = int(source.update_time_ms or 0)
    if updated <= 0:
        return True
    return int(time.time() * 1000) - updated > _STALE_GIT_SYNC_MS


def is_orphan_git_source_syncing(source: GitSourceDB) -> bool:
    """syncing 且从未完成过任一次同步（last_indexed_at_ms 仍为空）。"""
    if (source.last_index_status or "").strip().lower() != "syncing":
        return False
    return int(source.last_indexed_at_ms or 0) <= 0


def recover_interrupted_git_syncing_on_startup(db: Session) -> int:
    """进程启动时仅回收僵死 syncing，避免多副本误伤其他实例正在执行的后台任务。"""
    rows = db.query(GitSourceDB).filter(GitSourceDB.last_index_status == "syncing").all()
    n = 0
    for src in rows:
        if is_stale_git_source_sync(src):
            _set_git_source_sync_failed(db, src, _STALE_SYNC_RECOVER_MESSAGE)
            n += 1
        elif is_abandoned_orphan_git_sync(src):
            _set_git_source_sync_failed(db, src, _ORPHAN_SYNC_RECOVER_MESSAGE)
            n += 1
    return n


def recover_orphan_git_syncing_on_startup(db: Session) -> int:
    """兼容旧调用名。"""
    return recover_interrupted_git_syncing_on_startup(db)


def is_abandoned_orphan_git_sync(source: GitSourceDB) -> bool:
    """首次同步从未完成，且超过阈值无心跳（启动回收 / 列表修复 / 可打断同步）。"""
    if not is_orphan_git_source_syncing(source):
        return False
    updated = int(source.update_time_ms or 0)
    if updated <= 0:
        return True
    return int(time.time() * 1000) - updated > _LIST_ORPHAN_NO_HEARTBEAT_MS


def is_git_source_sync_blocking(source: GitSourceDB) -> bool:
    """仍为 syncing 且未僵死、非可打断孤儿 → 不应再开同步/删除。"""
    if (source.last_index_status or "").strip().lower() != "syncing":
        return False
    return not is_stale_git_source_sync(source) and not is_abandoned_orphan_git_sync(source)


def is_local_git_sync_running(source_id: str) -> bool:
    sid = (source_id or "").strip()
    if not sid:
        return False
    with _local_git_sync_lock:
        return sid in _local_git_sync_active


def register_local_git_sync(source_id: str) -> bool:
    """本进程登记同步任务；已登记则返回 False。后台 finally 释放；进程重启清空 set。"""
    sid = (source_id or "").strip()
    if not sid:
        return False
    with _local_git_sync_lock:
        if sid in _local_git_sync_active:
            return False
        _local_git_sync_active.add(sid)
        return True


def unregister_local_git_sync(source_id: str) -> None:
    sid = (source_id or "").strip()
    if not sid:
        return
    with _local_git_sync_lock:
        _local_git_sync_active.discard(sid)


def prepare_git_source_sync_start(db: Session, source: GitSourceDB) -> None:
    """HTTP 入队后台任务前：DB 互斥 + 本进程防双任务。"""
    if is_git_source_sync_blocking(source):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="该 Git 源正在同步中，请稍后再试",
        )
    if is_local_git_sync_running(source.id):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="该 Git 源正在同步中，请稍后再试",
        )
    if not register_local_git_sync(source.id):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="该 Git 源正在同步中，请稍后再试",
        )


def recover_stale_git_sources_for_user(db: Session, user_id: str) -> int:
    """列表打开时修复「无心跳的孤儿 syncing」（不回收仍有心跳的长任务）。返回修复条数。"""
    uid = (user_id or "").strip()
    if not uid:
        return 0
    rows = (
        db.query(GitSourceDB)
        .filter(GitSourceDB.created_by_user_id == uid)
        .filter(GitSourceDB.last_index_status == "syncing")
        .all()
    )
    if not rows:
        return 0
    n = 0
    for src in rows:
        if not is_abandoned_orphan_git_sync(src):
            continue
        _set_git_source_sync_failed(db, src, _ORPHAN_SYNC_RECOVER_MESSAGE)
        n += 1
    return n


def _set_git_source_sync_failed(db: Session, source: GitSourceDB, message: str) -> None:
    now_ms = int(time.time() * 1000)
    source.last_index_status = "failed"
    source.last_index_error = (message or "同步失败")[:2000]
    source.last_indexed_at_ms = now_ms
    source.update_time_ms = now_ms
    db.add(source)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def _resolve_skills_root(clone_root: Path, skills_subpath: str | None) -> Path:
    clone_resolved = clone_root.resolve()
    cleaned = assert_git_skills_subpath(skills_subpath)
    if cleaned is None:
        return clone_resolved
    rel = cleaned.replace("\\", "/").strip("/")
    root = (clone_root / rel).resolve()
    try:
        root.relative_to(clone_resolved)
    except ValueError as e:
        raise PublishError(
            code=400,
            error="invalid_skills_subpath",
            message="skills_subpath 必须位于克隆目录内",
        ) from e
    if not root.is_dir():
        raise PublishError(
            code=400,
            error="skills_subpath_not_found",
            message=f"仓库内未找到 skills_subpath 对应目录：{rel}",
        )
    return root


def _git_clone_at_ref(*, repo_url: str, ref: str, dest: Path, timeout: int = 300) -> str:
    host = (urlparse(repo_url).hostname or "").strip()
    if host:
        assert_resolved_git_host_allowed(host)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc_kw = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "env": env}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(dest), check=True, timeout=60, **proc_kw)
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=str(dest), check=True, timeout=60, **proc_kw)
        fetch = subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", "--", ref],
            cwd=str(dest),
            timeout=timeout,
            **proc_kw,
        )
        if fetch.returncode != 0:
            msg = (fetch.stderr or fetch.stdout or "").strip()
            tail = msg[:800] if msg else ""
            message = f"git fetch 失败：{tail}" if tail else "git fetch 失败"
            raise PublishError(
                code=400,
                error="git_fetch_failed",
                message=message,
            )
        subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=str(dest), check=True, timeout=120, **proc_kw)
        rr = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(dest), check=True, timeout=60, **proc_kw)
        sha = (rr.stdout or "").strip()
        if len(sha) < 7:
            raise PublishError(code=500, error="git_invalid_state", message="无法解析 HEAD commit")
        return sha
    except FileNotFoundError as e:
        raise PublishError(
            code=503,
            error="git_not_available",
            message="服务器未安装 git 或不在 PATH 中",
        ) from e
    except PublishError:
        raise
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or str(e) or "").strip()
        raise PublishError(
            code=400,
            error="git_clone_failed",
            message=f"git 操作失败：{msg[:800]}",
        ) from e


def _external_skill_id(*, source_id: str, entry_folder_name: str) -> str:
    return f"git:{source_id}:{entry_folder_name}"


def _git_entry_commit_unchanged(
    row: MarketAssetDB,
    *,
    git_source_id: str,
    head_sha: str,
) -> bool:
    prev_sha = (row.resolved_commit_sha or "").strip().lower()
    cur_sha = head_sha.strip().lower()
    if (row.storage_mode or "").strip().lower() != "git":
        return False
    if (row.git_source_id or "").strip() != (git_source_id or "").strip():
        return False
    return bool(prev_sha and cur_sha and prev_sha == cur_sha)


def _list_skill_entry_dirs(skills_root: Path) -> list[Path]:
    entry_dirs = sorted(
        [
            p
            for p in skills_root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name != "__MACOSX"
        ],
        key=lambda p: p.name,
    )
    rows: list[Path] = []
    for p in entry_dirs:
        if is_standard_skill_entry(p) or is_simple_skill_entry(p):
            rows.append(p)
    return rows


def _skill_import_item_from_prep_error(entry_name: str, exc: BaseException) -> SkillImportItemResult:
    if isinstance(exc, PublishError):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return SkillImportItemResult(
            entry=entry_name,
            status="error",
            error=str(detail.get("error") or "publish_failed"),
            message=str(detail.get("message") or exc),
        )
    if isinstance(exc, ValueError):
        return SkillImportItemResult(
            entry=entry_name,
            status="error",
            error="import_normalize_failed",
            message=str(exc),
        )
    if isinstance(exc, SQLAlchemyError):
        return SkillImportItemResult(
            entry=entry_name,
            status="error",
            error="publish_db_error",
            message=str(exc)[:500],
        )
    logger.exception("git sync prep failed entry=%s", entry_name)
    return SkillImportItemResult(
        entry=entry_name,
        status="error",
        error="prep_entry_failed",
        message=str(exc)[:500],
    )


def _merge_skill_import_results(
    data: SkillImportResponse,
    extra: list[SkillImportItemResult],
) -> SkillImportResponse:
    if not extra:
        return data
    results = [*data.results, *extra]
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    return SkillImportResponse(
        summary=SkillImportSummary(
            total=len(results),
            ok=ok,
            failed=failed,
            skipped=skipped,
        ),
        results=results,
    )


def _skill_import_response_from_results(results: list[SkillImportItemResult]) -> SkillImportResponse:
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    return SkillImportResponse(
        summary=SkillImportSummary(
            total=len(results),
            ok=ok,
            failed=failed,
            skipped=skipped,
        ),
        results=results,
    )


def _format_git_sync_index_error(data: SkillImportResponse) -> str:
    summary = (
        f"导入汇总：成功 {data.summary.ok}，跳过 {data.summary.skipped}，失败 {data.summary.failed}"
    )
    failed_items = [r for r in data.results if r.status == "error"]
    if not failed_items:
        return summary
    details = "\n".join(
        f"{r.entry}: {r.message or r.error or '失败'}" for r in failed_items
    )
    text = f"{summary}\n{details}"
    return text[:2000]


def _bind_git_asset(
    db: Session,
    *,
    plugin_id: str,
    git_source_id: str,
    external_id: str,
    resolved_sha: str,
    payload_sha256: str,
    declared_skill_version: str | None = None,
    commit: bool = False,
) -> None:
    try:
        asset = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == plugin_id).first()
        if not asset:
            raise PublishError(
                code=500,
                error="git_bind_failed",
                message=f"发布成功但未找到资产记录：{plugin_id}",
            )
        asset.git_source_id = git_source_id
        asset.external_id = external_id
        asset.resolved_commit_sha = resolved_sha
        s256 = (payload_sha256 or "").strip().lower()[:64]
        if s256:
            asset.git_sync_payload_sha256 = s256
        asset.storage_mode = "git"
        ds = (declared_skill_version or "").strip()[:64]
        asset.declared_skill_version = ds or None
        db.add(asset)
        if commit:
            db.commit()
    except PublishError:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise PublishError(
            code=500,
            error="git_bind_failed",
            message=f"绑定 Git 元数据失败：{exc}",
        ) from exc


def run_git_source_sync(
    *,
    db: Session,
    storage: S3StorageClient,
    user_id: str,
    source: GitSourceDB,
    fail_fast: bool = False,
    reraise: bool = True,
) -> GitSyncRunResponse | None:
    """克隆仓库、按字母序导入 skill；归一化 zip 的 SHA-256 与上次一致则跳过发布（commit 不变时亦跳过）。"""
    asset_repo = MarketAssetRepository(db)
    repo_url = _assert_public_git_http_url(source.repo_url)
    ref = assert_git_ref_branch_or_tag(source.ref or "main")
    author = parse_git_repo_owner(repo_url).strip() or _SIMPLE_AUTHOR_FALLBACK
    # HTTP 路由在入队后台任务前已 mark；脚本入口 create_git_source_and_sync 自行 mark。
    heartbeat = _GitSyncHeartbeat(db, source.id)

    clone_dir = Path(tempfile.mkdtemp(prefix="oj_git_clone_"))
    staging_dir = Path(tempfile.mkdtemp(prefix="oj_git_stage_"))
    try:
        head_sha = _git_clone_at_ref(repo_url=repo_url, ref=ref, dest=clone_dir, timeout=300)
        heartbeat.touch()
        skills_root = _resolve_skills_root(clone_dir, source.skills_subpath)
        entry_dirs = _list_skill_entry_dirs(skills_root)
        if not entry_dirs:
            raise PublishError(
                code=400,
                error="no_skills_in_repo",
                message="未在指定目录下找到有效 skill 条目",
            )
        if len(entry_dirs) > MAX_ZIP_ENTRIES:
            raise PublishError(
                code=400,
                error="too_many_skill_entries",
                message=f"skill 条目数 {len(entry_dirs)} 超过上限 {MAX_ZIP_ENTRIES}",
            )

        prep_payload_sha256: dict[str, str] = {}
        manifest_entry_extra: dict[str, dict] = {}
        declared_semver_by_entry: dict[str, str | None] = {}
        prep_failures: list[SkillImportItemResult] = []
        ready_entries: list[Path] = []

        for entry in entry_dirs:
            heartbeat.maybe_touch()
            name = entry.name
            try:
                declared_semver_by_entry[name] = resolve_skill_entry_declared_semver(entry)
                ext_id = _external_skill_id(source_id=source.id, entry_folder_name=name)
                existing = asset_repo.get_by_external_id(ext_id)
                if existing is not None and existing.publisher_id != user_id:
                    raise PublishError(
                        code=409,
                        error="git_external_id_conflict",
                        message=f"条目 {name} 已与另一发布者绑定，无法导入",
                    )

                ver = _resolve_git_entry_version(entry, head_sha)
                eo: dict = {
                    "version": ver,
                    "author": author,
                    "force": bool(existing),
                    "tags": [],
                    "version_desc": f"Git 同步 {source.name} @ {head_sha[:7]}",
                }
                if existing is not None:
                    eo["plugin_id"] = existing.asset_id

                publish_zip, _nm, _v = entry_to_publish_zip(
                    entry,
                    entry_key=name,
                    entry_overrides=eo,
                    version_fallback="0.0.1",
                    default_author=author,
                    default_tags=[],
                )
                try:
                    raw = publish_zip.read_bytes()
                    prep_payload_sha256[name] = hashlib.sha256(raw).hexdigest()
                finally:
                    publish_zip.unlink(missing_ok=True)

                manifest_entry_extra[name] = eo
                ready_entries.append(entry)
            except Exception as exc:
                prep_failures.append(_skill_import_item_from_prep_error(name, exc))
                if fail_fast:
                    break

        for sub in staging_dir.iterdir():
            if sub.is_dir():
                shutil.rmtree(sub, ignore_errors=True)
        for entry in ready_entries:
            dest = staging_dir / entry.name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(entry, dest, dirs_exist_ok=True)

        def _skip(name: str, _path: Path) -> tuple[bool, str | None]:
            ext_id = _external_skill_id(source_id=source.id, entry_folder_name=name)
            row = asset_repo.get_by_external_id(ext_id)
            if not row:
                return False, None
            prev_s256 = (row.git_sync_payload_sha256 or "").strip().lower()
            cur_s256 = prep_payload_sha256.get(name, "").strip().lower()
            # 同一解析 commit：仓库树未变；ZIP 字节可能因打包非确定性波动，不能仅依赖 payload 摘要。
            if _git_entry_commit_unchanged(
                row,
                git_source_id=source.id,
                head_sha=head_sha,
            ):
                return True, "git_sync_commit_unchanged"
            if prev_s256 and cur_s256 and prev_s256 == cur_s256:
                return True, "git_sync_payload_unchanged"
            return False, None

        def _after_ok(entry_name: str, pr):
            ext_id = _external_skill_id(source_id=source.id, entry_folder_name=entry_name)
            _bind_git_asset(
                db,
                plugin_id=pr.plugin_id,
                git_source_id=source.id,
                external_id=ext_id,
                resolved_sha=head_sha,
                payload_sha256=prep_payload_sha256.get(entry_name, ""),
                declared_skill_version=declared_semver_by_entry.get(entry_name),
                commit=True,
            )
            if (pr.publish_result or "") == PUBLISH_RESULT_REVIEWING:
                schedule_skill_publish_review(pr.plugin_id, pr.version, "git_sync")

        heartbeat.touch()
        if ready_entries:
            data = skill_import_from_staging_dir(
                staging_dir,
                user_id=user_id,
                db=db,
                storage=storage,
                force=False,
                fail_fast=fail_fast,
                manifest_entry_extra=manifest_entry_extra,
                entry_skip_predicate=lambda n, p: _skip(n, p),
                after_publish_success=_after_ok,
                on_entry_progress=lambda _name: heartbeat.maybe_touch(),
            )
            data = _merge_skill_import_results(data, prep_failures)
        else:
            data = _skill_import_response_from_results(prep_failures)
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise PublishError(
                code=500,
                error="git_bind_failed",
                message=f"提交 Git 绑定失败：{exc}",
            ) from exc

        now_ms = int(time.time() * 1000)
        if data.summary.failed > 0:
            if data.summary.ok > 0 or data.summary.skipped > 0:
                source.last_index_status = "partial_failure"
            else:
                source.last_index_status = "failed"
            source.last_index_error = _format_git_sync_index_error(data)
        else:
            source.last_index_status = "success"
            source.last_index_error = None
        source.last_indexed_at_ms = now_ms
        source.update_time_ms = now_ms
        db.add(source)
        db.commit()

        return GitSyncRunResponse(
            source_id=source.id,
            resolved_commit_sha=head_sha,
            skill_import=data,
        )
    except PublishError as e:
        now_ms = int(time.time() * 1000)
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        _finalize_git_source_sync_status(
            db,
            source.id,
            status="failed",
            error=str(e.detail.get("message") or e),
        )
        if reraise:
            raise e
        return None
    except Exception as exc:
        logger.exception("git sync unexpected error source_id=%s", source.id)
        _finalize_git_source_sync_status(
            db,
            source.id,
            status="failed",
            error=str(exc),
        )
        if reraise:
            raise exc
        return None
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)
        shutil.rmtree(staging_dir, ignore_errors=True)


def run_git_source_sync_background(
    *,
    source_id: str,
    user_id: str,
    fail_fast: bool = False,
) -> None:
    """后台执行 Git 同步（独立 DB Session）。须在路由侧已 register_local_git_sync。"""
    db = SessionLocal()
    src: GitSourceDB | None = None
    try:
        storage = get_storage_client()
        gs_repo = GitSourceRepository(db)
        src = gs_repo.get_by_id(source_id)
        if src is None:
            logger.warning("git sync background: source not found source_id=%s", source_id)
            return
        if (src.created_by_user_id or "").strip() != (user_id or "").strip():
            logger.warning(
                "git sync background: user mismatch source_id=%s expected=%s",
                source_id,
                user_id,
            )
            _set_git_source_sync_failed(db, src, "同步任务与 Git 源所有者不一致")
            return
        run_git_source_sync(
            db=db,
            storage=storage,
            user_id=user_id,
            source=src,
            fail_fast=fail_fast,
            reraise=False,
        )
    except Exception as exc:
        logger.exception("git sync background failed source_id=%s", source_id)
        if src is None:
            src = GitSourceRepository(db).get_by_id(source_id)
        if src is not None and (src.last_index_status or "").strip().lower() == "syncing":
            _set_git_source_sync_failed(db, src, f"后台同步异常：{exc}")
    finally:
        unregister_local_git_sync(source_id)
        db.close()


def delete_git_source_for_user(*, db: Session, user_id: str, source_id: str) -> None:
    gs_repo = GitSourceRepository(db)
    src = gs_repo.get_by_id(source_id)
    if src is None or (src.created_by_user_id or "").strip() != (user_id or "").strip():
        raise PublishError(
            code=403,
            error="forbidden",
            message="无权删除该 Git 源或资源不存在",
        )
    if is_local_git_sync_running(source_id) or is_git_source_sync_blocking(src):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="同步进行中，请稍后再删除",
        )
    if gs_repo.count_linked_assets(source_id) > 0:
        raise PublishError(
            code=409,
            error="git_source_has_assets",
            message="该 Git 源仍有关联 Skill，无法删除注册记录",
        )
    gs_repo.delete_by_id(source_id)
    db.commit()


def create_git_source(
    *,
    db: Session,
    user_id: str,
    name: str | None,
    repo_url: str,
    ref: str = "main",
    skills_subpath: str | None = None,
) -> GitSourceDB:
    """注册 Git 源（不执行同步）。全局唯一：规范化仓库 + 分支/tag + skills_subpath。"""
    gs_repo = GitSourceRepository(db)
    validated = _assert_public_git_http_url(repo_url)
    canonical = normalize_git_repo_global_key(validated)
    if not canonical:
        raise PublishError(code=400, error="invalid_repo_url", message="无效的仓库 URL")
    ref_clean = assert_git_ref_branch_or_tag(ref or "main")
    sub_clean = assert_git_skills_subpath(skills_subpath)
    dedup = compute_git_source_dedup_key(
        repo_url_canonical=canonical,
        ref=ref_clean,
        skills_subpath=sub_clean,
    )
    existing = gs_repo.find_global_by_dedup_key(dedup)
    if existing is not None:
        owner = (existing.created_by_user_id or "").strip()
        uid = (user_id or "").strip()
        if owner == uid and is_reclaimable_git_source(db, existing):
            now_ms = int(time.time() * 1000)
            existing.name = ((name or "").strip()[:128] or validated[:128])
            existing.repo_url = validated[:512]
            existing.repo_url_canonical = canonical[:640]
            existing.ref = ref_clean[:256]
            existing.skills_subpath = sub_clean
            existing.update_time_ms = now_ms
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing
        raise PublishError(
            code=409,
            error="git_repo_already_registered",
            message="该 Git 接入配置已被全局使用（同一仓库、分支/tag 与技能根路径），无法重复注册。",
        )
    now_ms = int(time.time() * 1000)
    label = (name or "").strip()[:128] or validated[:128]
    src = GitSourceDB(
        id=uuid.uuid4().hex,
        name=label,
        repo_url=validated[:512],
        repo_url_canonical=canonical[:640],
        git_source_dedup_key=dedup,
        ref=ref_clean[:256],
        skills_subpath=sub_clean,
        visibility_scope="private",
        created_by_user_id=user_id,
        create_time_ms=now_ms,
        update_time_ms=now_ms,
        last_index_status=None,
        last_index_error=None,
        last_indexed_at_ms=None,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def create_git_source_and_sync(
    *,
    db: Session,
    storage: S3StorageClient,
    user_id: str,
    name: str | None,
    repo_url: str,
    ref: str = "main",
    skills_subpath: str | None = None,
    fail_fast: bool = False,
) -> tuple[GitSourceDB, GitSyncRunResponse]:
    """创建 git_sources 并同步执行一次导入（测试/脚本用；HTTP API 请走后台同步）。"""
    src = create_git_source(
        db=db,
        user_id=user_id,
        name=name,
        repo_url=repo_url,
        ref=ref,
        skills_subpath=skills_subpath,
    )
    try:
        prepare_git_source_sync_start(db, src)
        mark_git_source_syncing(db, src)
        db.refresh(src)
        sync = run_git_source_sync(
            db=db,
            storage=storage,
            user_id=user_id,
            source=src,
            fail_fast=fail_fast,
            reraise=True,
        )
    finally:
        unregister_local_git_sync(src.id)
    if sync is None:
        raise PublishError(code=500, error="git_sync_failed", message="Git 同步未返回结果")
    return src, sync
