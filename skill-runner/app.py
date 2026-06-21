# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill_runner FastAPI 应用。

本地运行：
    uvicorn skill_runner.app:app --reload --port 8900
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse

from .config import settings
from .executor import get_executor
from .models import (
    CreateSessionRequest,
    CreateSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
    SessionStatus,
    sse_event,
)
from .session_store import store

logger = logging.getLogger("skill_runner")

router = APIRouter(prefix="/playground", tags=["playground"])
_executor = get_executor()

# ── 后台任务引用集：防止 GC 静默回收 fire-and-forget 任务 ──────────────────────
_bg_tasks: set[asyncio.Task] = set()


def _fire_and_track(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


# ── SSE 出站脱敏 ──────────────────────────────────────────────────────────────
# 对工具输出/文本里的密钥类敏感串做正则替换，避免不可信 skill 工具读到的
# api_key / token / Bearer 等经对话流外泄。值长度 ≥6 才匹配，避开 "token: 5" 这类误伤。
_SECRET_KV_RE = re.compile(
    r"(?i)(api[_-]?key|secret|access[_-]?key|password|passwd|token|authorization)"
    r"([\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9._\-]{6,})"
)
_SECRET_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{6,})")
# 协议/标识字段，非内容，不脱敏（避免破坏 tool 名、角色名等）
_REDACT_SKIP_KEYS = frozenset({"type", "role", "member", "name", "code", "team_name"})


def _redact_text(s: str) -> str:
    # 先 Bearer 再 KV：否则 "Authorization: Bearer <tok>" 会被 KV 误把 "Bearer"
    # 当值替换，漏掉后面真正的 token
    s = _SECRET_BEARER_RE.sub(lambda m: f"{m.group(1)}***", s)
    s = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", s)
    return s


def _redact_event(ev: dict) -> dict:
    return {
        k: (_redact_text(v) if isinstance(v, str) and k not in _REDACT_SKIP_KEYS else v)
        for k, v in ev.items()
    }


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    # ---- 0) 并发上限：控制面级保护，避免 session 无限堆积（对所有 executor 生效）----
    if await store.active_count() >= settings.max_concurrent_sessions:
        raise HTTPException(
            status_code=429,
            detail=f"max concurrent sessions reached ({settings.max_concurrent_sessions})",
        )

    # ---- 1) 从请求体中的文本字段组装 SkillBundle ----
    # marketplace proxy 已在转发前解析 ZIP、把文本内容填入请求体，
    # skill-runner 不做任何网络请求，直接使用。
    from .skill_loader import SkillBundle

    system_prompt = req.system_prompt
    logger.info(
        "CREATE SESSION: skill_id=%s skill_md=%dB system_prompt=%dB",
        req.skill_id, len(req.skill_md), len(req.system_prompt),
    )
    skill_bundle: SkillBundle | None = None

    if req.skill_md:
        # 用传入的文本字段重建 SkillBundle（无 package_bytes，沙箱 provision 只需文本）
        # Decode base64 ZIP bytes if provided by proxy
        pkg_bytes = b""
        if req.package_bytes_b64:
            import base64
            try:
                pkg_bytes = base64.b64decode(req.package_bytes_b64)
            except Exception:  # noqa: BLE001
                logger.warning("failed to decode package_bytes_b64 for %s", req.skill_id)
        skill_bundle = SkillBundle(
            asset_id=req.skill_id,
            version=req.version,
            skill_md=req.skill_md,
            workflow_md=req.workflow_md,
            roles=req.roles,
            team_mode=req.team_mode,
            package_bytes=pkg_bytes,
        )
        if not system_prompt:
            system_prompt = skill_bundle.system_prompt_text()
        logger.info(
            "skill content received: %s@%s skill_md=%dB workflow_md=%dB roles=%d",
            req.skill_id, req.version,
            len(req.skill_md), len(req.workflow_md), len(req.roles),
        )
    elif not system_prompt and req.skill_id:
        logger.warning(
            "no skill content in request for %s@%s; session will run without skill context",
            req.skill_id, req.version,
        )

    # ---- 2) create session ----
    session = await store.create(req.skill_id, req.version, req.skill_type)
    session.system_prompt = system_prompt or ""
    session.user_id = req.user_id
    if skill_bundle is not None:
        session.extra["skill_bundle"] = skill_bundle

    # Pod 冷启动（wait_ready + provision）最长 240s，同步等待会触发 nginx 60s 超时。
    # 后台任务：立即返回 session_id（status=starting），pod 就绪后经 SSE 推 ready 事件。
    _fire_and_track(_init_session(session))

    return CreateSessionResponse(
        session_id=session.session_id,
        status=session.status,
        timeout_seconds=settings.session_timeout_seconds,
    )


async def _init_session(session) -> None:
    try:
        await _executor.create(session)
        session.status = SessionStatus.READY
        await session.events.put(sse_event("ready"))
        _llm_warmup = getattr(_executor, "llm_warmup", None)
        if _llm_warmup is not None:
            _fire_and_track(_llm_warmup(session))
    except Exception as exc:  # noqa: BLE001
        logger.exception("session create failed: %s", session.session_id)
        session.status = SessionStatus.ERROR
        await session.events.put(sse_event("error", code="create_failed", message=str(exc)))
        await session.events.put(sse_event("done"))


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(session_id: str, req: SendMessageRequest) -> SendMessageResponse:
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == SessionStatus.STARTING:
        raise HTTPException(status_code=503, detail="session still initializing")
    if session.status in (SessionStatus.DONE, SessionStatus.ERROR):
        raise HTTPException(status_code=410, detail="session already ended")
    if len(req.content) > settings.message_max_chars:
        raise HTTPException(status_code=400, detail="message too long")
    if session.turn_count >= settings.message_max_turns:
        raise HTTPException(status_code=429, detail="turn limit reached")

    session.turn_count += 1
    session.status = SessionStatus.ACTIVE
    session.touch()

    _fire_and_track(_drive_turn(session_id, req.content))
    return SendMessageResponse(message_id=f"msg-{uuid.uuid4().hex[:12]}")


async def _drive_turn(session_id: str, content: str) -> None:
    session = await store.get(session_id)
    if session is None:
        return
    try:
        async for event in _executor.run_turn(session, content):
            await session.events.put(_redact_event(event))
            session.touch()
    except Exception as exc:  # noqa: BLE001
        logger.exception("turn failed: %s", session_id)
        await session.events.put(sse_event("error", code="turn_failed", message=_redact_text(str(exc))))
    finally:
        # executor.run_turn 不再吐 done，由本函数负责
        await session.events.put(sse_event("done"))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """查询会话状态。状态主要经 SSE 推送，这里提供一次性查询补充。"""
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session.session_id,
        "status": session.status,
        "skill_type": session.skill_type,
        "turn_count": session.turn_count,
        "timeout_seconds": settings.session_timeout_seconds,
    }


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str) -> StreamingResponse:
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def gen():
        event_id = 0
        try:
            while True:
                try:
                    event = await asyncio.wait_for(session.events.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                except asyncio.CancelledError:
                    break
                event_id += 1
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {event_id}\ndata: {payload}\n\n"
                # done = 一轮结束，连接不关闭以支持多轮；
                # session_ended = 会话结束，断开连接
                if event.get("type") in ("done", "session_ended"):
                    if event.get("type") == "session_ended":
                        break
        except asyncio.CancelledError:
            pass
        finally:
            logger.debug("SSE gen exiting for session=%s", session_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/sessions/{session_id}/beacon")
async def end_session_beacon(session_id: str) -> Response:
    """供前端 sendBeacon 调用（浏览器 unload 只能发 POST）。逻辑与 DELETE 完全一致。"""
    return await end_session(session_id)


@router.delete("/sessions/{session_id}")
async def end_session(session_id: str) -> Response:
    session = await store.get(session_id)
    if session is not None:
        # 通知 SSE stream 连接可以退出了
        try:
            session.events.put_nowait(sse_event("session_ended"))
        except asyncio.QueueFull:
            logger.debug(
                "end_session: SSE queue full when emitting session_ended for %s",
                session_id,
            )
        except Exception:  # noqa: BLE001 - best-effort 通知，失败不应阻断 destroy
            logger.warning(
                "end_session: failed to enqueue session_ended for %s",
                session_id,
                exc_info=True,
            )
        await _executor.destroy(session)
    else:
        logger.warning("end_session: session %s not found in store (pod may have leaked)", session_id)
    await store.end(session_id)
    from .llm_proxy import clear_session_tokens
    clear_session_tokens(session_id)
    return Response(status_code=204)


# ---- 应用装配 ----



async def _idle_session_reaper(interval_seconds: int = 60) -> None:
    """周期性回收空闲超时的会话。

    pod 墙钟 activeDeadline 到点会置 pod 为 Failed，但 store 里的 session 不会自动
    消失、SSE 长连接也悬着。本协程按 last_active_at 兜底：通知 SSE 退出、删 pod、
    从 store 移除，避免悬挂会话占用全局并发额度。"""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            now = time.time()
            for session in await store.snapshot():
                if session.status in (SessionStatus.DONE, SessionStatus.ERROR):
                    continue
                if now - session.last_active_at < settings.session_timeout_seconds:
                    continue
                logger.info(
                    "idle reaper: ending stale session %s (idle %.0fs)",
                    session.session_id, now - session.last_active_at,
                )
                if session.events is not None:
                    try:
                        session.events.put_nowait(sse_event("session_ended"))
                    except Exception:  # noqa: BLE001 - 通知 best-effort
                        pass
                try:
                    await _executor.destroy(session)
                except Exception:  # noqa: BLE001
                    logger.warning("idle reaper: destroy failed for %s", session.session_id)
                await store.end(session.session_id)
                from .llm_proxy import clear_session_tokens
                clear_session_tokens(session.session_id)
        except Exception:  # noqa: BLE001 - 周期任务不能因单次异常退出
            logger.exception("idle reaper error (non-fatal)")


@asynccontextmanager
async def lifespan(api_app: FastAPI):
    # 日志脱敏：控制面持真 LLM key + pod token，启动即挂 filter 擦掉日志里的凭证
    from .log_redaction import configure_sensitive_log_redaction
    configure_sensitive_log_redaction()

    # 会话编排状态（session_store / token_registry / 限流计数）按设计驻留进程内，
    # 控制面以单副本运行（见 Deployment replicas: 1）。
    logger.info("skill-runner started: in-process session state, single-replica control plane")

    # pool_size>0 时后台预热，ephemeral 模式（=0）跳过。
    # 后台而非阻塞：阻塞会让 /health 探活在 lifespan 返回前连续失败 → liveness 误杀 →
    # 重启清空 token_registry → 在飞 worker token 全部失效（401）。后台预热则
    # /health 立即可用，池未热时 acquire 按需新建 pod，不影响功能。
    warm = getattr(_executor, "warm", None)
    if warm is not None:
        async def _warm_bg() -> None:
            try:
                await warm()
                logger.info("executor pool warmed")
            except Exception:  # noqa: BLE001
                logger.warning("executor pool warm failed, continuing without pre-warmed pods")
        _fire_and_track(_warm_bg())

    # K8s executor: 启动孤儿 pod reaper（裸 Pod 不支持 ttlSecondsAfterFinished）
    reaper_task = None
    start_reaper = getattr(_executor, "start_pod_reaper", None)
    if start_reaper is not None:
        reaper_task = asyncio.create_task(start_reaper(interval_seconds=120))
        logger.info("pod reaper task started")

    # 空闲会话回收：对所有 executor 生效（控制面 store 级，与 pod reaper 互补）
    idle_task = asyncio.create_task(_idle_session_reaper())
    logger.info("idle session reaper started")

    yield

    # 关闭时取消 reaper 并清空池
    if reaper_task is not None:
        reaper_task.cancel()
    idle_task.cancel()
    aclose = getattr(_executor, "aclose", None)
    if aclose is not None:
        await aclose()


app = FastAPI(title="skill-runner", version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")

# k8s executor 的 pod 经此代理调 LLM（真 key 留控制面）
from .llm_proxy import proxy_router  # noqa: E402

app.include_router(proxy_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "executor": settings.executor}
