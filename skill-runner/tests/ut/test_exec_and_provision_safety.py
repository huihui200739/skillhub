# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""安全单测：!cmd 走 exec+shlex 无 shell 注入;
worker 重复 provision 拒绝覆盖。

    pytest skill-runner/tests/ut/test_exec_and_provision_safety.py -q
"""
from __future__ import annotations

# pylint: disable=protected-access
import asyncio
import os

# worker/app.py 在 import 时快照 WORKER_AUTH_TOKEN，必须早于导入设定
os.environ.setdefault("WORKER_AUTH_TOKEN", "secret-token")

import httpx

from skill_runner.executor.local import LocalExecutor
from skill_runner.models import Session
from skill_runner.worker import app as worker_app


# 命令注入：!cmd 用 create_subprocess_exec + shlex.split，不经 shell

class _FakeProc:
    returncode = 0

    async def communicate(self):
        return (b"safe; echo unsafe\n", None)

    def kill(self):
        pass


def test_exec_local_uses_exec_not_shell_no_injection():
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        return _FakeProc()

    orig = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec  # type: ignore[assignment]
    try:
        async def go():
            ex = LocalExecutor()
            sess = Session(skill_id="d", version="v", workspace="")
            return [ev async for ev in ex._exec_local(sess, "echo safe; echo unsafe")]
        events = asyncio.run(go())
    finally:
        asyncio.create_subprocess_exec = orig  # type: ignore[assignment]

    # 关键：`;` 未被解释为命令分隔符，而是作为字面参数传给 echo（无 shell 注入）
    assert captured["args"] == ["echo", "safe;", "echo", "unsafe"]
    assert "tool_result" in [e.get("type") for e in events]


# worker provision 幂等保护：已 provision 时重复 provision → 409

class _CountingExecutor:
    def __init__(self) -> None:
        self.create_calls = 0

    async def create(self, session: Session) -> None:
        self.create_calls += 1
        session.workspace = "/tmp/fake"


def test_worker_provision_rejects_double_provision():
    async def go():
        fake = _CountingExecutor()
        worker_app.reset_for_testing(executor=fake, session=None)
        transport = httpx.ASGITransport(app=worker_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pod") as c:
            h = {"Authorization": "Bearer secret-token"}
            r1 = await c.post("/provision", json={"skill_md": "# S"}, headers=h)
            r2 = await c.post("/provision", json={"skill_md": "# S2"}, headers=h)
            return r1.status_code, r2.status_code, fake.create_calls
    s1, s2, calls = asyncio.run(go())
    assert s1 == 200
    assert s2 == 409     # 已 provision：重复请求被拒，不静默销毁已有 session
    assert calls == 1    # executor.create 只调一次，旧 session 未被覆盖
