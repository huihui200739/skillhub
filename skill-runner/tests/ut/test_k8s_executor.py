# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""K8sExecutor 自检：ephemeral 路径 + pool 路径。

运行：
    pytest skill-runner/tests/ut/test_k8s_executor.py        # 推荐：pytest 从仓库根运行
    python skill-runner/tests/ut/test_k8s_executor.py       # 快速手跑，依赖根 conftest.py 注册名字
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from skill_runner.executor.k8s import K8sExecutor
from skill_runner.executor.pod_manager import PodManager
from skill_runner.executor.pod_pool import PodPoolManager
from skill_runner.llm_proxy import token_registry
from skill_runner.models import Session

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class FakePodManager(PodManager):
    def __init__(self) -> None:
        self.created: dict[str, dict[str, str]] = {}
        self.deleted: list[str] = []
        self._n = 0

    async def create_pod(self, pod_id: str, env: dict[str, str]) -> str:
        self._n += 1
        name = f"pod-{pod_id}-{self._n}"
        self.created[name] = env
        return name

    async def wait_ready(self, name: str, timeout: float) -> str:
        return f"10.0.0.{self._n}"

    async def delete_pod(self, name: str) -> None:
        self.deleted.append(name)


def _fake_pod_app(captured: dict) -> FastAPI:
    app = FastAPI()

    @app.post("/provision")
    async def provision(request: Request):
        captured["provision_auth"] = request.headers.get("authorization")
        captured["provision"] = await request.json()
        return {"ok": True}

    @app.post("/run_turn")
    async def run_turn(request: Request):
        captured["run_turn_auth"] = request.headers.get("authorization")
        body = await request.json()
        captured["query"] = body.get("query")

        async def gen():
            for ev in (
                {"type": "text", "delta": "hi "},
                {"type": "text", "delta": "there"},
                {"type": "answer", "content": "hi there"},
            ):
                yield json.dumps(ev) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/reset")
    async def reset(request: Request):
        captured["reset_auth"] = request.headers.get("authorization")
        captured["reset_called"] = True
        return {"status": "reset"}

    return app


async def _run_ephemeral(pods: FakePodManager, pod_client: httpx.AsyncClient) -> list[str]:
    """即用即弃路径：create→run_turn→destroy，pod 被删，token 被撤。"""
    captured: dict = {}
    app = _fake_pod_app(captured)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), timeout=10)
    ex = K8sExecutor(pod_manager=pods, pod_client=client)

    session = Session(skill_id="demo", version="1.0.0", workspace="")
    session.system_prompt = "do the thing"
    failures: list[str] = []

    await ex.create(session)
    if not session.workspace.startswith("k8spod://"):
        failures.append(f"ephemeral: workspace wrong: {session.workspace!r}")
    token = session.extra.get("pod_token")
    worker_token = session.extra.get("worker_auth_token")
    if not token or not worker_token:
        failures.append("ephemeral: missing tokens")
    if token and token_registry.resolve(token) != session.session_id:
        failures.append("ephemeral: token not registered")
    if worker_token and captured.get("provision_auth") != f"Bearer {worker_token}":
        failures.append("ephemeral: provision missing worker auth")

    events = [ev async for ev in ex.run_turn(session, "go")]
    if [e.get("type") for e in events] != ["text", "text", "answer"]:
        failures.append(f"ephemeral: events wrong: {[e.get('type') for e in events]}")
    if worker_token and captured.get("run_turn_auth") != f"Bearer {worker_token}":
        failures.append("ephemeral: run_turn missing worker auth")

    pod_name = session.extra.get("pod_name")
    await ex.destroy(session)
    if pod_name and pod_name not in pods.deleted:
        failures.append("ephemeral: pod not deleted on destroy")
    if token and token_registry.resolve(token) is not None:
        failures.append("ephemeral: token not revoked on destroy")

    await client.aclose()
    return failures


async def _run_pooled(pods: FakePodManager, pod_client: httpx.AsyncClient) -> list[str]:
    """预热池路径：acquire→provision→run_turn→destroy(reset+release)。"""
    captured: dict = {}
    app = _fake_pod_app(captured)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), timeout=10)

    def _env_builder(worker_token: str, llm_token: str) -> dict:
        return {
            "WORKER_AUTH_TOKEN": worker_token,
            "LLM_API_KEY": llm_token,
            "SESSION_ID": "pooled",
        }

    pool = PodPoolManager(pods, _env_builder, pool_size=1, max_idle=1)
    await pool.warm()
    ex = K8sExecutor(pod_manager=pods, pod_client=client, pool=pool)

    session = Session(skill_id="demo", version="1.0.0", workspace="")
    session.system_prompt = "pooled skill"
    failures: list[str] = []

    initial_idle = pool.idle_count()
    if initial_idle != 1:
        failures.append(f"pooled: warm should give 1 idle, got {initial_idle}")

    await ex.create(session)
    if pool.idle_count() != 0:
        failures.append(f"pooled: after acquire idle should be 0, got {pool.idle_count()}")
    lease = session.extra.get("pod_lease")
    if lease is None:
        failures.append("pooled: no pod_lease in session.extra")
        await client.aclose()
        return failures

    llm_token = session.extra.get("pod_token")
    worker_token = session.extra.get("worker_auth_token")
    # per-pod token should be bound to current session
    if llm_token and token_registry.resolve(llm_token) != session.session_id:
        failures.append("pooled: llm_token not bound to session")
    if worker_token and captured.get("provision_auth") != f"Bearer {worker_token}":
        failures.append("pooled: provision missing worker auth")

    events = [ev async for ev in ex.run_turn(session, "pooled-go")]
    if [e.get("type") for e in events] != ["text", "text", "answer"]:
        failures.append(f"pooled: events wrong: {[e.get('type') for e in events]}")

    await ex.destroy(session)
    # /reset must have been called with worker token
    if not captured.get("reset_called"):
        failures.append("pooled: /reset not called on destroy")
    if worker_token and captured.get("reset_auth") != f"Bearer {worker_token}":
        failures.append("pooled: reset missing worker auth")
    # token unbound after destroy
    if llm_token and token_registry.resolve(llm_token) is not None:
        failures.append("pooled: llm_token not unbound after destroy")
    # pod returned to pool (healthy reset)
    if pool.idle_count() != 1:
        failures.append(f"pooled: pod should be back in pool after healthy destroy, idle={pool.idle_count()}")

    await pool.shutdown()
    await client.aclose()
    return failures


async def main() -> int:
    pod_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_fake_pod_app({})), timeout=10
    )

    failures: list[str] = []
    failures += await _run_ephemeral(FakePodManager(), pod_client)
    failures += await _run_pooled(FakePodManager(), pod_client)

    await pod_client.aclose()

    if failures:
        logger.info("RESULT: FAIL")
        for f in failures:
            logger.info("  -", f)
        return 1
    logger.info("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


def test_k8s_executor() -> None:
    """pytest 入口：跳 main() 设为同步函数供 pytest 发现。"""
    rc = asyncio.run(main())
    assert rc == 0, f"test_k8s_executor self-check failed (rc={rc})"
