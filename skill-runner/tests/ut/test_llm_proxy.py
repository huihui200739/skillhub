# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""控制面 LLM 代理自检：token 鉴权 + 真 key 注入 + 透传转发。

运行：
    pytest skill-runner/tests/ut/test_llm_proxy.py        # 推荐：pytest 从仓库根运行
    python skill-runner/tests/ut/test_llm_proxy.py       # 快速手跑，依赖根 conftest.py 注册名字
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

from skill_runner import llm_proxy
from skill_runner.config import settings


async def main() -> int:
    # ---- arrange：用 MockTransport 假冒上游 LLM，捕获它收到的请求 ----
    captured: dict = {}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "echo": "upstream"})

    llm_proxy.set_forward_client_for_testing(
        httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    )
    # settings 是 frozen dataclass；测试内用 object.__setattr__ 绕过
    object.__setattr__(settings, "llm_api_key", "REALKEY-do-not-leak")
    object.__setattr__(settings, "llm_api_base", "https://upstream.test/v3")

    app = FastAPI()
    app.include_router(llm_proxy.proxy_router)
    token = llm_proxy.token_registry.issue("sess-abc")

    transport = httpx.ASGITransport(app=app)
    failures: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 1) 合法 token：转发成功，且上游收到的是真 key（不是 token），路径拼接正确
        r = await c.post(
            "/internal/llm/chat/completions",
            headers={"authorization": f"Bearer {token}"},
            json={"model": "x", "messages": []},
        )
        if r.status_code != 200:
            failures.append(f"valid token: expected 200, got {r.status_code}")
        if captured.get("auth") != "Bearer REALKEY-do-not-leak":
            failures.append(f"key injection: upstream saw {captured.get('auth')!r}, want real key")
        if not captured.get("url", "").endswith("/v3/chat/completions"):
            failures.append(f"path join: upstream url = {captured.get('url')!r}")

        # 2) 错误 token：401，且绝不转发
        captured.clear()
        r2 = await c.post(
            "/internal/llm/chat/completions",
            headers={"authorization": "Bearer bogus"},
            json={},
        )
        if r2.status_code != 401:
            failures.append(f"bad token: expected 401, got {r2.status_code}")
        if captured:
            failures.append("bad token: must NOT forward upstream")

        # 3) 缺 token：401
        r3 = await c.post("/internal/llm/chat/completions", json={})
        if r3.status_code != 401:
            failures.append(f"missing token: expected 401, got {r3.status_code}")

        # 4) revoke 后失效
        llm_proxy.token_registry.revoke_for_session("sess-abc")
        r4 = await c.post(
            "/internal/llm/chat/completions",
            headers={"authorization": f"Bearer {token}"},
            json={},
        )
        if r4.status_code != 401:
            failures.append(f"revoked token: expected 401, got {r4.status_code}")

    if failures:
        logger.info("RESULT: FAIL")
        for f in failures:
            logger.info("  - %s", f)
        return 1
    logger.info("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


def test_llm_proxy() -> None:
    """pytest 入口：跳 main() 设为同步函数供 pytest 发现。"""
    rc = asyncio.run(main())
    assert rc == 0, f"test_llm_proxy self-check failed (rc={rc})"
