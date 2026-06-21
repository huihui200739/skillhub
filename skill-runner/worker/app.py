# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""pod 内 worker FastAPI 应用。一个 pod 服务一个 session。

    /healthz   - k8s readiness 探针
    /provision - 写入 skill 内容 + 初始化 agent（控制面 pod ready 后调一次）
    /run_turn  - 跑一轮，NDJSON 流式吐事件给控制面
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..log_redaction import configure_sensitive_log_redaction
from ..models import Session
from ..skill_loader import SkillBundle

logger = logging.getLogger("skill_runner.worker")


# 过滤 /healthz 探针请求，避免每 10s 一条 access log 刷屏
class _HealthzFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /healthz" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HealthzFilter())

# 日志脱敏：worker 持 per-pod token，启动即擦掉日志里的凭证
configure_sensitive_log_redaction()


class ProvisionRequest(BaseModel):
    system_prompt: str = ""
    skill_md: str = ""
    workflow_md: str = ""
    roles: dict[str, str] = {}
    team_mode: str = ""
    package_bytes_b64: str = ""
    skill_type: str = "ordinary"


class RunTurnRequest(BaseModel):
    query: str


app = FastAPI(title="skill-agent-worker", version="0.1.0")

_session: Session | None = None
_executor = None

# 启动时检查关键安全配置
_WORKER_AUTH_TOKEN = os.environ.get("WORKER_AUTH_TOKEN", "")
if not _WORKER_AUTH_TOKEN:
    logger.error(
        "WORKER_AUTH_TOKEN not set — /provision /run_turn /files /file are UNPROTECTED; "
        "set this env var in production"
    )
if os.environ.get("SKILL_RUNNER_ALLOW_DEBUG_SHELL", "").lower() == "true":
    logger.warning(
        "SKILL_RUNNER_ALLOW_DEBUG_SHELL=true: !cmd debug shell is ENABLED — "
        "any authenticated user can execute arbitrary shell commands; disable in production"
    )


def _check_worker_auth(authorization: str | None) -> None:
    if not _WORKER_AUTH_TOKEN:
        # 配置缺失时模块级已报 ERROR，runtime 放行（兼容本地/测试场景）
        return
    if authorization != f"Bearer {_WORKER_AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def reset_for_testing(executor=None, session=None) -> None:
    """测试钩子：重置 worker 模块内部状态。生产代码不应调用。"""
    global _executor, _session
    _executor = executor
    _session = session


def get_executor():
    global _executor
    if _executor is None:
        from ..executor.local import LocalExecutor

        _executor = LocalExecutor()
    return _executor


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "provisioned": _session is not None}


@app.post("/provision")
async def provision(req: ProvisionRequest, authorization: str | None = Header(default=None)) -> dict:
    _check_worker_auth(authorization)
    global _session
    import base64

    pkg = b""
    if req.package_bytes_b64:
        try:
            pkg = base64.b64decode(req.package_bytes_b64)
        except Exception:  # noqa: BLE001
            logger.warning("bad package_bytes_b64; ignoring")

    session = Session(
        skill_id=os.environ.get("SESSION_ID", "pod"),
        version="",
        workspace="",
        skill_type=req.skill_type,
    )
    session.system_prompt = req.system_prompt
    if req.skill_md or pkg:
        session.extra["skill_bundle"] = SkillBundle(
            asset_id=session.skill_id,
            version="",
            skill_md=req.skill_md,
            workflow_md=req.workflow_md,
            roles=req.roles,
            team_mode=req.team_mode,
            package_bytes=pkg,
        )

    await get_executor().create(session)
    _session = session
    return {"status": "ready"}


@app.post("/reset")
async def reset(authorization: str | None = Header(default=None)) -> dict:
    """池复用：清掉当前 session 的沙箱+agent，pod 保持 warm 等下一个 /provision。"""
    _check_worker_auth(authorization)
    global _session
    if _session is not None:
        try:
            await get_executor().destroy(_session)
        except Exception:  # noqa: BLE001
            logger.exception("reset destroy failed")
        _session = None
    return {"status": "reset"}


@app.post("/run_turn")
async def run_turn(req: RunTurnRequest, authorization: str | None = Header(default=None)) -> StreamingResponse:
    _check_worker_auth(authorization)
    if _session is None:
        raise HTTPException(status_code=409, detail="not provisioned")
    executor = get_executor()
    session = _session

    async def gen():
        # agent 事件流喂进队列，主循环带超时取——静默期满 keepalive_seconds 吐一条
        # keepalive，保证控制面→pod 的 NDJSON 流不被 httpx read 超时掐断
        # （deep agent 初始化/teammate 启动可静默 >2 分钟）
        keepalive = settings.worker_keepalive_seconds
        queue: asyncio.Queue = asyncio.Queue()
        done_sentinel = object()

        async def _produce():
            try:
                async for ev in executor.run_turn(session, req.query):
                    await queue.put(ev)
            except Exception as exc:  # noqa: BLE001
                logger.exception("run_turn failed")
                await queue.put(
                    {"type": "error", "code": "worker_failed", "message": str(exc)}
                )
            finally:
                await queue.put(done_sentinel)

        producer = asyncio.create_task(_produce())
        ka_count = 0
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=keepalive)
                except asyncio.TimeoutError:
                    ka_count += 1
                    # 静默期心跳，打日志便于验证（仅静默时触发，不刷屏）
                    logger.info(
                        "keepalive #%d sent (stream idle >%.0fs)", ka_count, keepalive
                    )
                    yield json.dumps({"type": "keepalive"}) + "\n"
                    continue
                if ev is done_sentinel:
                    break
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        finally:
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    return StreamingResponse(gen(), media_type="application/x-ndjson")
