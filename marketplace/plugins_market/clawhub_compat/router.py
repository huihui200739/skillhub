"""ClawHub CLI-compatible routes on the same FastAPI app and port as marketplace (under /api/v1)."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
import io
import logging
import zipfile
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from plugins_market.clawhub_compat import mappers
from plugins_market.clawhub_compat.fingerprint import hash_skill_zip, sanitize_zip_path
from plugins_market.core.config import settings
from plugins_market.core.database import get_db
from plugins_market.core.errors import PublishError
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.repositories import MarketAssetVersionRepository
from plugins_market.schemas.plugin import PluginListQuery
from plugins_market.core.moderation import MODERATION_APPROVED, moderation_coalesce_display
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.services.plugin import (
    get_download_info,
    get_plugin_version_detail_service,
    list_plugins_service,
)
from plugins_market.validation.constants import MAX_FILE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter()

CLAWHUB_RESOLVE_MAX_VERSIONS = 25
CLAWHUB_RESOLVE_FAILURE_RATIO_THRESHOLD = 0.5
CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS = 600.0
CLAWHUB_INSPECT_FILE_MAX_BYTES = 10 * 1024 * 1024
# Native ClawHub CLI clamps --limit to a max (200); larger values do not 422 — they truncate.
CLAWHUB_LIMIT_CAP = 200


def _clamp_clawhub_limit(n: int) -> int:
    return min(max(n, 1), CLAWHUB_LIMIT_CAP)


def _plugin_type_filter() -> Optional[str]:
    pt = (settings.clawhub_plugin_type or "").strip()
    return pt if pt else None


def _safe_error_detail(default: str, detail: Any = None) -> str:
    """Return a short public-safe error message."""
    if isinstance(detail, str):
        msg = detail.strip()
        return msg if msg else default
    if isinstance(detail, dict):
        for key in ("message", "error", "detail"):
            candidate = detail.get(key)
            if isinstance(candidate, str):
                msg = candidate.strip()
                if msg:
                    return msg[:200]
    return default


def _sync_fetch_bytes(url: str) -> bytes:
    timeout = httpx.Timeout(
        CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS,
        connect=min(30.0, CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS),
    )
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        body = r.content
    if len(body) > MAX_FILE_SIZE:
        raise OSError(f"artifact exceeds MAX_FILE_SIZE ({MAX_FILE_SIZE} bytes)")
    return body


async def _open_upstream_stream(
    *,
    url: str,
    timeout: httpx.Timeout,
) -> tuple[httpx.AsyncClient, AbstractAsyncContextManager[httpx.Response], httpx.Response]:
    """
    Open upstream stream and fail fast on non-2xx before response headers are sent.
    Returns (client, stream_cm, response) that caller must close.
    """
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    stream_cm = client.stream("GET", url)
    try:
        resp = await stream_cm.__aenter__()
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail="artifact upstream unavailable") from e

    if resp.status_code < 200 or resp.status_code >= 300:
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(status_code=502, detail="artifact upstream returned non-2xx")

    return client, stream_cm, resp


def _find_list_item(
    slug: str,
    *,
    db: Session,
    storage: Any,
):
    pt = _plugin_type_filter()
    direct = list_plugins_service(
        PluginListQuery(page=1, page_size=1, asset_id=slug, plugin_type=pt),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    if direct.items:
        return direct.items[0]
    fuzzy = list_plugins_service(
        PluginListQuery(page=1, page_size=CLAWHUB_LIMIT_CAP, search_keyword=slug, plugin_type=pt),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    for it in fuzzy.items:
        if it.asset_id == slug:
            return it
    return None


@router.get("/search")
def clawhub_search(
    q: str = Query(..., min_length=1),
    limit: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    page_size = _clamp_clawhub_limit(limit or 25)
    pt = _plugin_type_filter()
    data = list_plugins_service(
        PluginListQuery(
            page=1,
            page_size=page_size,
            search_keyword=q.strip(),
            order_by="install_count",
            desc=True,
            plugin_type=pt,
        ),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    results = [mappers.search_result_row(it) for it in data.items]
    return {"results": results}


@router.get("/skills")
def clawhub_explore(
    limit: int = Query(25, ge=1),
    sort: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    order_by, desc = mappers.explore_sort_to_marketplace(sort)
    pt = _plugin_type_filter()
    page_size = _clamp_clawhub_limit(limit)
    data = list_plugins_service(
        PluginListQuery(
            page=1,
            page_size=page_size,
            order_by=order_by,
            desc=desc,
            plugin_type=pt,
        ),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    items = [mappers.explore_item(it) for it in data.items]
    return {"items": items, "nextCursor": None}


@router.get("/skills/{slug}")
def clawhub_skill_meta(
    slug: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise HTTPException(status_code=404, detail="skill not found")
    eff_ver = (item.public_latest_version or item.latest_version or "").strip()
    if not eff_ver:
        raise HTTPException(status_code=404, detail="skill has no published version")
    detail = get_plugin_version_detail_service(
        item.asset_id,
        eff_ver,
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    return mappers.skill_detail_bundle(item, detail)


@router.get("/skills/{slug}/versions")
def clawhub_skill_versions(
    slug: str = Path(..., min_length=1),
    limit: int = Query(50, ge=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise HTTPException(status_code=404, detail="skill not found")
    vrepo = MarketAssetVersionRepository(db)
    cap = _clamp_clawhub_limit(limit)
    rows = vrepo.list_versions(slug)[:cap]
    rows = [
        r
        for r in rows
        if moderation_coalesce_display(getattr(r, "moderation_status", None)) == MODERATION_APPROVED
    ]
    out = [
        mappers.version_list_item(
            r.version,
            r.changelog,
            int(r.create_time or 0),
        )
        for r in rows
    ]
    return {"items": out, "nextCursor": None}


@router.get("/skills/{slug}/versions/{version}")
async def clawhub_skill_version_detail(
    slug: str = Path(..., min_length=1),
    version: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise HTTPException(status_code=404, detail="skill not found")
    vrepo = MarketAssetVersionRepository(db)
    row = vrepo.get_version(asset_id=slug, version=version)
    if not row:
        raise HTTPException(status_code=404, detail="version not found")
    detail = get_plugin_version_detail_service(slug, version, db, storage, viewer=ANONYMOUS_VIEWER)
    files: list[dict[str, Any]] = []
    try:
        dl = get_download_info(
            asset_id=slug,
            version=version,
            db=db,
            storage=storage,
            fetch_user_id=None,
            viewer=ANONYMOUS_VIEWER,
        )
        zip_bytes = await asyncio.to_thread(_sync_fetch_bytes, dl.download_url)
        file_rows, _fp = hash_skill_zip(zip_bytes)
        files = [
            {"path": str(fr["path"]), "sha256": str(fr["sha256"]), "size": int(fr["size"])}
            for fr in file_rows
        ]
    except Exception as e:
        logger.warning("clawhub version files listing failed slug=%s version=%s: %s", slug, version, e)

    return mappers.skill_version_row(
        detail,
        files,
        created_ms=int(row.create_time or 0),
    )


@router.get("/skills/{slug}/file")
async def clawhub_skill_file(
    slug: str = Path(..., min_length=1),
    path: str = Query(..., min_length=1, description="Path inside the zip bundle"),
    version: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise HTTPException(status_code=404, detail="skill not found")
    ver = (version or item.public_latest_version or item.latest_version or "").strip()
    if not ver:
        raise HTTPException(status_code=404, detail="version required")
    try:
        dl = get_download_info(
            asset_id=slug,
            version=ver,
            db=db,
            storage=storage,
            fetch_user_id=None,
            viewer=ANONYMOUS_VIEWER,
        )
        zip_bytes = await asyncio.to_thread(_sync_fetch_bytes, dl.download_url)
    except PublishError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=_safe_error_detail("artifact lookup failed", e.detail),
        ) from e

    want = sanitize_zip_path(path.replace("\\", "/"))
    if not want:
        raise HTTPException(status_code=400, detail="invalid path")

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for raw in zf.namelist():
            if raw.endswith("/"):
                continue
            if sanitize_zip_path(raw) == want:
                raw_data = zf.read(raw)
                cap = int(CLAWHUB_INSPECT_FILE_MAX_BYTES)
                if len(raw_data) > cap:
                    raise HTTPException(status_code=413, detail="file too large for inspect")
                text = raw_data.decode("utf-8", errors="replace")
                return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
    raise HTTPException(status_code=404, detail="path not found in bundle")


@router.get("/download")
async def clawhub_download(
    slug: str = Query(..., min_length=1),
    version: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    try:
        info = get_download_info(
            asset_id=slug,
            version=version,
            db=db,
            storage=storage,
            fetch_user_id=None,
            viewer=ANONYMOUS_VIEWER,
        )
    except PublishError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=_safe_error_detail("artifact lookup failed", e.detail),
        ) from e

    url = info.download_url
    timeout = httpx.Timeout(
        CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS,
        connect=min(30.0, CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS),
    )
    client, stream_cm, resp = await _open_upstream_stream(url=url, timeout=timeout)

    async def body():
        try:
            async for chunk in resp.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()

    safe_name = (info.name or "skill").replace('"', "")
    filename = f"{safe_name}_{info.version}.zip"
    return StreamingResponse(
        body(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/resolve")
async def clawhub_resolve(
    slug: str = Query(..., min_length=1),
    fingerprint: str = Query(..., alias="hash", min_length=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        return {"match": None, "latestVersion": None}

    vrepo = MarketAssetVersionRepository(db)
    rows = [
        r
        for r in vrepo.list_versions(slug)
        if moderation_coalesce_display(getattr(r, "moderation_status", None)) == MODERATION_APPROVED
    ]
    if not rows:
        return {"match": None, "latestVersion": None}

    latest = rows[0]
    latest_payload = {"version": latest.version}

    want = fingerprint.strip().lower()
    max_n = int(CLAWHUB_RESOLVE_MAX_VERSIONS)
    match_ver: Optional[str] = None
    checked_count = 0
    failed_count = 0

    for row in rows[:max_n]:
        try:
            dl = get_download_info(
                asset_id=slug,
                version=row.version,
                db=db,
                storage=storage,
                fetch_user_id=None,
                viewer=ANONYMOUS_VIEWER,
            )
            zip_bytes = await asyncio.to_thread(_sync_fetch_bytes, dl.download_url)
            _files, fp = hash_skill_zip(zip_bytes)
            checked_count += 1
            if fp.lower() == want:
                match_ver = row.version
                break
        except Exception as e:
            failed_count += 1
            logger.warning("clawhub resolve skip version %s: %s", row.version, e)
            continue

    attempted = checked_count + failed_count
    if match_ver is None and failed_count > 0 and attempted > 0:
        failure_ratio = failed_count / attempted
        if failure_ratio >= CLAWHUB_RESOLVE_FAILURE_RATIO_THRESHOLD:
            raise HTTPException(status_code=502, detail="resolve failed due to upstream artifact errors")

    return {
        "match": {"version": match_ver} if match_ver else None,
        "latestVersion": latest_payload,
    }
