# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""worker 自检：注入 fake executor，验证 /healthz /provision /run_turn 流。

运行：
    pytest skill-runner/tests/ut/test_worker.py        # 推荐：pytest 从仓库根运行
    python skill-runner/tests/ut/test_worker.py       # 快速手跑，依赖根 conftest.py 注册名字
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

# 【重要】worker/app.py 会在 import 时快照 WORKER_AUTH_TOKEN 进模块变量，之后修改 env 无效。
# 所以必须在 import skill_runner.worker.app 之前先设。
os.environ.setdefault("WORKER_AUTH_TOKEN", "secret-token")

import httpx

from skill_runner.models import Session
from skill_runner.worker import app as worker_app

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class FakeExecutor:
    def __init__(self) -> None:
        self.created: Session | None = None
        self.queries: list[str] = []
        self.destroyed: bool = False

    async def create(self, session: Session) -> None:
        session.workspace = "/tmp/fake-workspace"
        self.created = session

    async def run_turn(self, session: Session, query: str):
        self.queries.append(query)
        for ev in (
            {"type": "text", "delta": "答"},
            {"type": "answer", "content": "答"},
        ):
            yield ev

    async def destroy(self, session: Session) -> None:
        self.destroyed = True


async def main() -> int:
    fake = FakeExecutor()
    worker_app.reset_for_testing(executor=fake, session=None)
    # WORKER_AUTH_TOKEN 已在模块顶部设定，必须早于 import worker.app

    transport = httpx.ASGITransport(app=worker_app.app)
    failures: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://pod") as c:
        headers = {"Authorization": "Bearer secret-token"}
        # 1) no/incorrect auth -> 401 before any session state is checked
        r_no_auth = await c.post("/run_turn", json={"query": "x"})
        if r_no_auth.status_code != 401:
            failures.append(f"run_turn without auth: expected 401, got {r_no_auth.status_code}")

        # 2) not provisioned yet, but auth is valid -> 409
        r = await c.post("/run_turn", json={"query": "x"}, headers=headers)
        if r.status_code != 409:
            failures.append(f"run_turn before provision: expected 409, got {r.status_code}")


        h = (await c.get("/healthz")).json()
        if h.get("provisioned") is not False:
            failures.append(f"healthz before provision: {h}")

        # 3) provision
        r2_bad = await c.post("/provision", json={"system_prompt": "be a skill", "skill_md": "# Skill"})
        if r2_bad.status_code != 401:
            failures.append(f"provision without auth: expected 401, got {r2_bad.status_code}")
        r2 = await c.post("/provision", json={"system_prompt": "be a skill", "skill_md": "# Skill"}, headers=headers)
        if r2.status_code != 200:
            failures.append(f"provision: expected 200, got {r2.status_code}")
        if fake.created is None or fake.created.system_prompt != "be a skill":
            failures.append("executor.create not called with provisioned session")
        if "skill_bundle" not in (fake.created.extra if fake.created else {}):
            failures.append("skill_bundle not built from provision payload")
        h2 = (await c.get("/healthz")).json()
        if h2.get("provisioned") is not True:
            failures.append(f"healthz after provision: {h2}")

        # 3) run_turn -> NDJSON 事件
        events: list[dict] = []
        async with c.stream("POST", "/run_turn", json={"query": "go"}, headers=headers) as resp:
            if resp.status_code != 200:
                failures.append(f"run_turn: expected 200, got {resp.status_code}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        if fake.queries != ["go"]:
            failures.append(f"executor.run_turn query wrong: {fake.queries}")
        if [e.get("type") for e in events] != ["text", "answer"]:
            failures.append(f"relayed events wrong: {[e.get('type') for e in events]}")

        # 4) reset，池复用：清 session，pod 保持 warm
        rr_no_auth = await c.post("/reset")
        if rr_no_auth.status_code != 401:
            failures.append(f"reset without auth: expected 401, got {rr_no_auth.status_code}")
        rr = await c.post("/reset", headers=headers)
        if rr.status_code != 200:
            failures.append(f"reset: expected 200, got {rr.status_code}")
        if not getattr(fake, "destroyed", False):
            failures.append("reset did not call executor.destroy")
        h3 = (await c.get("/healthz")).json()
        if h3.get("provisioned") is not False:
            failures.append(f"healthz after reset: {h3}")

    os.environ.pop("WORKER_AUTH_TOKEN", None)

    if failures:
        logger.info("RESULT: FAIL")
        for f in failures:
            logger.info("  - %s", f)
        return 1
    logger.info("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


def test_worker() -> None:
    """pytest 入口：跳 main() 设为同步函数供 pytest 发现。"""
    rc = asyncio.run(main())
    assert rc == 0, f"test_worker self-check failed (rc={rc})"
