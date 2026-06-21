# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Playground 反向代理：marketplace 的 /api/v1/playground/* 透明转发到独立部署的
skill-runner 微服务（settings.skill_runner_url）。

marketplace 与 skill-runner 是不同进程、不同 venv（agent-core 仅在 skill-runner 侧），
通过 HTTP 通信。SSE（GET .../stream）必须流式透传，不能缓冲。

认证策略：
- POST /sessions、GET /quota：要求登录，前者检查每日配额
- 其余路径：透明转发，session 已在创建时鉴权
"""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.config import settings
from plugins_market.core.database import get_db
from plugins_market.core.logging import get_logger
from plugins_market.core.rate_limit import SlidingWindowRateLimiter
from plugins_market.repositories.playground_quota_repository import PlaygroundQuotaRepository

logger = get_logger(__name__)

# 每用户每分钟发消息限流：进程内滑窗
_msg_rate_limiter = SlidingWindowRateLimiter()

# idle reaper 回收 session 不经此代理，_session_still_alive 验活兜底，避免用户因
# 进程内记录过期而永远无法新建。_user_create_locks 保护"检查→创建→记录"原子性，
# 防止同一用户并发请求绕过限制。
_user_active_session: dict[str, set[str]] = {}     # user_id → {session_id, ...}
_session_owner: dict[str, str] = {}                # session_id → user_id
_user_create_locks: dict[str, asyncio.Lock] = {}   # user_id → per-user Lock

# 验活复用同一个 client（进程级单例），避免每次 TOCTOU 路径都新建连接池
_probe_client: httpx.AsyncClient | None = None


def _get_probe_client() -> httpx.AsyncClient:
    global _probe_client
    if _probe_client is None:
        _probe_client = httpx.AsyncClient(timeout=5.0)
    return _probe_client


def _runner_url(path: str = "") -> str:
    """拼 skill-runner 上游 URL。path 允许以 / 开头或不开头。"""
    base = settings.skill_runner_url.rstrip("/")
    if not path:
        return f"{base}/api/v1/playground"
    return f"{base}/api/v1/playground/{path.lstrip('/')}"


def _runner_unavailable() -> HTTPException:
    """控制面不可达时统一报错体，避免多处 detail 字面量重复。"""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "skill_runner_unavailable",
                "message": "控制面暂不可达，请稍后重试"},
    )


def _release_session(session_id: str) -> None:
    """DELETE / beacon / 验活失效时调用，释放并发计数。"""
    user_id = _session_owner.pop(session_id, None)
    if user_id:
        sids = _user_active_session.get(user_id)
        if sids is not None:
            sids.discard(session_id)
            if not sids:
                _user_active_session.pop(user_id, None)


def _inject_user_id(body: bytes, user_id: str) -> bytes:
    """把 user_id 注入 JSON body 供 skill-runner token 计量。

    解析或序列化失败时返回原 body（保留旧行为：转发由 skill-runner 自行处理），
    但记一条 warning 便于排查 Content-Type 异常或上游格式问题。
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("playground proxy: cannot decode body to inject user_id: %s", exc)
        return body
    if not isinstance(payload, dict):
        logger.warning("playground proxy: body is not a JSON object, skip user_id inject")
        return body
    payload["user_id"] = user_id
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


async def _session_still_alive(session_id: str) -> bool:
    """向 skill-runner 验活，处理 idle reaper 已回收但 proxy 未感知的情况。

    返回语义（注意：这是「活着且可继续使用吗」，不是单纯死活探测）：
      - True  → 会话仍可继续使用，调用方应阻塞新建（避免占额外配额）
      - False → 会话已可释放（404 / done / error）或确认 skill-runner 死了，调用方可清理记录并放行

    错误区分：
      - skill-runner 在线、明确返回 4xx/5xx 但不是 404 → 偏保守视作仍活着（防误清理）
      - skill-runner 拒连 / DNS / 超时 / 5xx → 进一步看一次健康检查；如果整面挂了，整体拒绝新建以保护下游（控制面挂时新开 session 也跑不动）
      - 404 → 真正失活（reaper 回收完毕）
      - 2xx with status in {done, error} → 已结束
    """
    client = _get_probe_client()
    try:
        r = await client.get(_runner_url(f"sessions/{session_id}"))
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        # 探测失败：不放行（之前是返回 False 乐观放行，控制面短暂挂掉时
        # 会让用户绕过 max_concurrent_sessions / 配额，把已有 session 留成幽灵）
        logger.warning("skill-runner probe network error for session=%s; deny new", session_id)
        raise _runner_unavailable() from exc
    except httpx.HTTPError as exc:
        # 其它 httpx 异常（协议错误等）也走拒绝新建的保守路径
        logger.warning("skill-runner probe http error: %s: %s", type(exc).__name__, exc)
        raise _runner_unavailable() from exc

    if r.status_code == 404:
        return False  # idle reaper 已回收，proxy 端清记录、放行新建
    if r.status_code >= 500:
        # 控制面 5xx：偏保守不放行；让用户重试或联系运维
        logger.warning("skill-runner probe 5xx for session=%s status=%s", session_id, r.status_code)
        raise _runner_unavailable()
    if r.status_code >= 400:
        # 4xx（非 404）罕见：当作仍活着，避免误清理；用户可手动 DELETE 释放
        logger.warning("skill-runner probe 4xx for session=%s status=%s; treat as alive",
                       session_id, r.status_code)
        return True
    try:
        status_val = r.json().get("status")
    except ValueError:
        # 2xx 但 body 不是 JSON：当作仍活着（json.JSONDecodeError 是 ValueError 子类）
        return True
    return status_val not in ("done", "error")


router = APIRouter(prefix="/playground", tags=["playground"])


# ── Helpers ────────────────────────────────────────────────────────────────────

# team_mode 推导规则（与 TeamAgentSpec 的自动推导对齐）：
#   有 roles/*.md + roles 里有 count 范围  → "hybrid"（保留预定义名单，同时允许 spawn_member）
#   有 roles/*.md + 无 count 范围          → ""（skill-runner 侧返回 "predefined"，锁定名单）
#   无 roles/*.md                           → ""（skill-runner 侧返回 None → 框架自动推导 "default"）
#   frontmatter 显式声明 team_mode          → 尊重声明（最高优先级）
_VALID_TEAM_MODES = frozenset({"default", "predefined", "hybrid"})


def _extract_team_mode(skill_md: str) -> str:
    """从 SKILL.md frontmatter 推导 team_mode；无信号/解析失败返回 ""（由 skill-runner 侧决策）。

    推导优先级：
    1. frontmatter 显式 team_mode 字段（合法值：default / predefined / hybrid）
    2. roles 列表中任意 role.count 为列表（范围，如 [0, 9]）→ "hybrid"
       roles/*.md 已提供预定义名单，count 范围表示 leader 还需动态 spawn 更多实例，
       与 TeamAgentSpec.team_mode="hybrid" 语义完全对齐（保留名单 + 允许 spawn_member）。

    解析容错：Playground 入口可能接受预览版/草稿 skill，frontmatter 不一定保证合法。
    解析失败时记 warning 并返回 ""，由 skill-runner 侧 default 推导兜底，但运营可
    通过日志感知到「试用入口收到了非法 skill」。
    """
    if not skill_md:
        return ""
    try:
        from plugins_market.validation.types.skill import parse_skill_frontmatter

        fm, _ = parse_skill_frontmatter(skill_md.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 草稿/预览版 skill 可能 frontmatter 不合法
        logger.warning(
            "playground: skill frontmatter parse failed, fallback to default team_mode: %s: %s",
            type(exc).__name__, exc,
        )
        return ""
    # ① 显式声明
    val = fm.get("team_mode")
    if isinstance(val, str) and val.strip().lower() in _VALID_TEAM_MODES:
        return val.strip().lower()
    # ② count 范围推导：任意 role 的 count 为列表 → hybrid
    roles = fm.get("roles")
    if isinstance(roles, list):
        for role in roles:
            if isinstance(role, dict) and isinstance(role.get("count"), list):
                return "hybrid"
    return ""


def _parse_skill_zip(data: bytes) -> tuple[str, str, dict[str, str], str]:
    """Parse ZIP bytes → (skill_md, workflow_md, roles, team_mode).

    按文件 basename 匹配（支持任意嵌套层级）。复用 marketplace 发布期同款 ZIP 安全校验
    （validate_zip_safety / safe_read_zip_member / DecompressCounter），防 zip bomb。
    team_mode 取自 SKILL.md frontmatter（opt-in 动态团队），未声明则为 ""。
    """
    from plugins_market.validation.zip_utils import (
        DecompressCounter,
        safe_read_zip_member,
        validate_zip_safety,
    )

    skill_md = workflow_md = ""
    roles: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            validate_zip_safety(zf)  # 元数据预检，异常包快速拒绝
            counter = DecompressCounter()  # 跨成员累计解压字节，超限即 abort
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.replace("\\", "/").split("/")
                basename_lower = parts[-1].lower()
                parent_lower = parts[-2].lower() if len(parts) >= 2 else ""
                # 只关心这三类文本，其余成员不读，省解压
                want = (
                    (basename_lower == "skill.md" and not skill_md)
                    or (basename_lower == "workflow.md" and not workflow_md)
                    or (parent_lower == "roles" and basename_lower.endswith(".md"))
                )
                if not want:
                    continue
                try:
                    content = safe_read_zip_member(zf, info.filename, counter).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - 单成员读失败/超限则跳过该成员
                    continue
                if basename_lower == "skill.md" and not skill_md:
                    skill_md = content
                elif basename_lower == "workflow.md" and not workflow_md:
                    workflow_md = content
                elif parent_lower == "roles" and basename_lower.endswith(".md"):
                    role_name = parts[-1].rsplit(".", 1)[0]
                    roles[role_name] = content
    except zipfile.BadZipFile:
        pass
    except Exception as exc:  # noqa: BLE001 - validate_zip_safety 等抛错：拒绝注入，返回空
        logger.warning("playground proxy: zip safety check failed, skip injection: %s", exc)
        return "", "", {}, ""
    return skill_md, workflow_md, roles, _extract_team_mode(skill_md)


async def _inject_skill_content(body: bytes) -> bytes:
    """POST /sessions 转发前解析 skill ZIP 并把文本内容注入请求体。

    marketplace 用 DB + S3 权限下载 ZIP、解析出文本字段后转发；skill-runner 收到的
    是纯文本，无需对外发起请求，也不持有任何凭证。比让 skill-runner 自行下载省一次跨网络 I/O。
    """
    try:
        payload = json.loads(body)
    except Exception:
        return body

    skill_id = payload.get("skill_id", "")
    version = payload.get("version", "latest")
    # 已有内容 / 无 skill_id / 调用方已自行提供 system_prompt 时跳过
    if not skill_id or payload.get("skill_md") or payload.get("system_prompt"):
        return body

    try:
        from plugins_market.core.database import SessionLocal
        from plugins_market.core.s3_storage_client import get_storage_client
        from plugins_market.repositories.market_assets_repository import (
            MarketAssetRepository,
            MarketAssetVersionRepository,
        )
        from plugins_market.services.skill_review_runtime import (
            build_package_name,
            download_archive,
            resolve_archive_key,
        )
    except ImportError as exc:
        logger.warning("playground proxy: import failed, skip skill content injection: %s", exc)
        return body

    storage = get_storage_client()
    if storage is None:
        logger.warning("playground proxy: storage client not ready, skip skill content injection")
        return body

    try:
        import tempfile
        from pathlib import Path

        with SessionLocal() as db:
            asset = MarketAssetRepository(db).get_by_asset_id(skill_id)
            if asset is None:
                logger.warning("playground proxy: asset not found: %s", skill_id)
                return body
            version_repo = MarketAssetVersionRepository(db)
            if not version or version == "latest":
                row = version_repo.get_latest_version(asset_id=skill_id)
            else:
                row = version_repo.get_version(asset_id=skill_id, version=version)
            if row is None:
                logger.warning("playground proxy: version not found: %s@%s", skill_id, version)
                return body
            version_moderation = getattr(row, "moderation_status", None)
            if version_moderation not in (None, "APPROVED"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "skill_not_approved",
                        "message": f"该 Skill 版本尚未通过审核（状态：{version_moderation}），无法在 Playground 中执行",
                    },
                )
            package_name = build_package_name(asset, row)
            archive_key = resolve_archive_key(storage, row.file_path, package_name)
            resolved_version = getattr(row, "version", version)

        # S3 下载是同步 blocking I/O，放到线程池避免阻塞事件循环
        def _download() -> bytes:
            with tempfile.TemporaryDirectory(prefix="playground_proxy_") as tmp:
                local = Path(tmp) / package_name
                download_archive(storage, archive_key, local)
                return local.read_bytes()

        zip_bytes = await asyncio.to_thread(_download)

        # ZIP 解析：marketplace 侧解析（marketplace venv 里没有 agent-core）
        skill_md, workflow_md, roles, team_mode = _parse_skill_zip(zip_bytes)
        payload["skill_md"] = skill_md
        payload["workflow_md"] = workflow_md
        payload["roles"] = roles
        # 仅在 skill 显式声明时透传；"" 表示交给 skill-runner 自动推导，老 skill 零影响
        if team_mode:
            payload["team_mode"] = team_mode
        payload["version"] = resolved_version
        # Also inject the full ZIP as base64 so skill-runner can extract all files
        import base64
        payload["package_bytes_b64"] = base64.b64encode(zip_bytes).decode("ascii")
        logger.info(
            "playground proxy: injected skill content %s@%s skill_md=%dB workflow_md=%dB roles=%d team_mode=%s zip=%dB",
            skill_id, resolved_version, len(skill_md), len(workflow_md), len(roles), team_mode or "-", len(zip_bytes),
        )
    except HTTPException:
        # 审核拦截等显式 HTTP 错误必须透传给调用方，不能被下面的兜底吞掉
        # （否则未过审 skill 会被静默放行、403 失效）。
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("playground proxy: skill content injection failed for %s: %s", skill_id, exc)

    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# 逐跳（hop-by-hop）头部：转发时必须剔除，由各自的 HTTP 连接自行协商。
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


def _filtered_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _next_midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.isoformat()


async def _forward(
    request: Request,
    target: str,
    body: bytes,
    extra_headers: dict | None = None,
    *,
    stream: bool = False,
) -> Response:
    """向 skill-runner 转发请求。

    stream=True 时返回 StreamingResponse（SSE 等长连接），
    stream=False 时为普通请求/响应模式。
    """
    fwd_headers = _filtered_headers(request.headers)
    if extra_headers:
        fwd_headers.update(extra_headers)

    timeout = httpx.Timeout(
        connect=5.0,
        read=None if stream else 300.0,
        write=30.0,
        pool=10.0,
    )

    if stream:
        client = httpx.AsyncClient(timeout=timeout)
        upstream_req = client.build_request(
            request.method,
            target,
            params=request.query_params,
            headers=fwd_headers,
            content=body,
        )
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning("playground stream proxy unreachable: %s (url=%s)", exc, target)
            return Response(status_code=502, content=b"skill-runner unreachable")

        async def _body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _body_iter(),
            status_code=upstream.status_code,
            headers=_filtered_headers(upstream.headers),
            media_type=upstream.headers.get("content-type"),
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                request.method,
                target,
                params=request.query_params,
                headers=fwd_headers,
                content=body,
            )
    except httpx.HTTPError as exc:
        logger.warning("playground proxy unreachable: %s (url=%s)", exc, target)
        return Response(status_code=502, content=b"skill-runner unreachable")

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filtered_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


# ── Authenticated routes (registered before catch-all) ────────────────────────

@router.get("/quota")
async def playground_quota(
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """返回当前用户今日配额使用情况。"""
    limit = settings.playground_daily_limit
    reset_at = _next_midnight_utc()

    if auth.is_admin:
        return JSONResponse({
            "used": 0,
            "limit": limit,
            "is_unlimited": True,
            "reset_at": reset_at,
        })

    used = PlaygroundQuotaRepository(db).get_usage(auth.acting_user_id)
    return JSONResponse({
        "used": used,
        "limit": limit,
        "is_unlimited": limit == 0,
        "reset_at": reset_at,
    })


@router.post("/sessions")
async def create_session(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> Response:
    """创建 Playground session，含鉴权、并发检查（含 TOCTOU 锁）与每日配额检查。"""
    body = await request.body()

    if auth.is_admin:
        # admin 无并发限制，直接走注入 + 转发
        body = await _inject_skill_content(body)
        body = _inject_user_id(body, auth.acting_user_id)
        return await _forward(request, _runner_url("sessions"), body)

    # 非 admin：per-user 锁串行化"检查→配额→注入→创建→记录"，消除 TOCTOU
    if auth.acting_user_id not in _user_create_locks:
        _user_create_locks[auth.acting_user_id] = asyncio.Lock()

    async with _user_create_locks[auth.acting_user_id]:
        # 1) 并发检查：先剔除已死 session，再按上限判定
        max_concurrent = settings.playground_max_concurrent_sessions
        for sid in list(_user_active_session.get(auth.acting_user_id, set())):
            if not await _session_still_alive(sid):
                _release_session(sid)  # idle reaper 已回收，清理过期记录
        active = _user_active_session.get(auth.acting_user_id, set())
        if max_concurrent > 0 and len(active) >= max_concurrent:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "session_conflict",
                    "message": f"您已有 {len(active)} 个活跃的 Playground 会话（上限 {max_concurrent}），请先结束部分会话再新建",
                    "active_session_ids": sorted(active),
                },
            )

        # 2) 每日配额
        limit = settings.playground_daily_limit
        reset_at = _next_midnight_utc()
        quota_repo = None
        extra_resp_headers: dict[str, str] = {}
        if limit > 0:
            quota_repo = PlaygroundQuotaRepository(db)
            allowed, used = quota_repo.try_increment(auth.acting_user_id, limit)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "quota_exceeded",
                        "message": f"每日 Playground 试用次数已达上限 {limit} 次，明日 0 点重置",
                        "used": used,
                        "limit": limit,
                    },
                    headers={
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": reset_at,
                    },
                )
            extra_resp_headers = {
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(limit - used),
                "X-RateLimit-Reset": reset_at,
            }

        # 3) 注入 skill 内容（ZIP → 文本字段），inject 失败退回配额
        try:
            body = await _inject_skill_content(body)
        except Exception:
            if quota_repo is not None:
                try:
                    quota_repo.decrement(auth.acting_user_id)
                except Exception:  # noqa: BLE001
                    logger.warning("quota refund after inject failure failed for %s", auth.acting_user_id)
            raise

        # 4) 注入 user_id 供 skill-runner token 计量
        body = _inject_user_id(body, auth.acting_user_id)

        # 5) 转发
        resp = await _forward(request, _runner_url("sessions"), body)
        for k, v in extra_resp_headers.items():
            resp.headers[k] = v

        # 6) 记录新 session（仅 2xx）
        if resp.status_code < 300:
            try:
                new_sid = json.loads(resp.body).get("session_id", "") if resp.body else ""
            except (json.JSONDecodeError, AttributeError) as exc:
                logger.warning("playground proxy: cannot parse session create response: %s", exc)
                new_sid = ""
            if new_sid:
                _user_active_session.setdefault(auth.acting_user_id, set()).add(new_sid)
                _session_owner[new_sid] = auth.acting_user_id

        return resp


# ── Per-user message rate limit (registered before catch-all) ─────────────────

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
) -> Response:
    """发送消息：透明转发前做每用户每分钟限流。"""
    per_min = settings.playground_message_rate_per_minute
    if per_min > 0 and not auth.is_admin:
        if not _msg_rate_limiter.allow(
            f"pg-msg:{auth.acting_user_id}", limit=per_min, window_sec=60.0
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limited",
                    "message": f"发送过于频繁，每分钟最多 {per_min} 条，请稍后再试",
                },
                headers={"Retry-After": "60"},
            )
    body = await request.body()
    return await _forward(request, _runner_url(f"sessions/{session_id}/messages"), body)


# ── Session lifecycle: explicit routes to maintain concurrent-session counter ──

@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
) -> Response:
    """结束 session：校验归属后转发 DELETE，并释放 proxy 侧并发计数。"""
    if not auth.is_admin:
        owner = _session_owner.get(session_id)
        if owner is not None and owner != auth.acting_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "not_your_session", "message": "无权终止他人的 Playground 会话"},
            )
    body = await request.body()
    resp = await _forward(request, _runner_url(f"sessions/{session_id}"), body)
    _release_session(session_id)
    return resp


@router.post("/sessions/{session_id}/beacon")
async def session_beacon(session_id: str, request: Request) -> Response:
    """浏览器 unload sendBeacon：与 DELETE 等效，释放并发计数。"""
    body = await request.body()
    resp = await _forward(request, _runner_url(f"sessions/{session_id}/beacon"), body)
    _release_session(session_id)
    return resp


# ── Transparent catch-all proxy ────────────────────────────────────────────────

@router.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def playground_proxy(path: str, request: Request) -> Response:
    body = await request.body()
    # SSE 长连接（GET .../stream）需要流式透传，其余记为普通转发。
    is_stream = request.method == "GET" and path.endswith("/stream")
    return await _forward(request, _runner_url(path), body, stream=is_stream)
