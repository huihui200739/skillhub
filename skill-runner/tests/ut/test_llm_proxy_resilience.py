# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""控制面 LLM 代理单测：上游失败返回显式 error 数据行 + [DONE]；每日 token 预算
超限在转发前拦截；Redis 预算 store 与单/多实例切换。

    pytest skill-runner/tests/ut/test_llm_proxy_resilience.py -q
"""
from __future__ import annotations

# pylint: disable=protected-access
import asyncio

import httpx
from fastapi import FastAPI

from skill_runner import llm_proxy
from skill_runner.config import settings


def _app_client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(llm_proxy.proxy_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_tool_call_upstream_failure_returns_explicit_error_stream():
    async def go():
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("upstream down")

        llm_proxy.set_forward_client_for_testing(
            httpx.AsyncClient(transport=httpx.MockTransport(boom))
        )
        object.__setattr__(settings, "llm_api_key", "REALKEY")
        object.__setattr__(settings, "llm_api_base", "https://up.test/v1")
        token = llm_proxy.token_registry.issue("sess-tool", "u-tool")
        try:
            async with _app_client() as c:
                r = await c.post(
                    "/internal/llm/chat/completions",
                    headers={"authorization": f"Bearer {token}"},
                    json={"model": "x", "messages": [], "tools": [{"type": "function"}]},
                )
                return r.status_code, r.text
        finally:
            llm_proxy.token_registry.revoke_for_session("sess-tool")
    code, body = asyncio.run(go())
    assert code == 200
    assert '"error"' in body   # 明确 error 数据行，OpenAI 流式客户端据此抛错、本轮明确失败可重试
    assert "[DONE]" in body


def test_daily_token_budget_blocks_over_limit_before_forwarding():
    async def go():
        object.__setattr__(settings, "user_daily_token_limit", 100)
        object.__setattr__(settings, "llm_api_key", "REALKEY")
        store = llm_proxy.MemoryUserBudgetStore()
        llm_proxy.reset_user_budget_store_for_testing(store)
        await store.add("u-cap", 999)  # 预置该用户当日已超预算

        called = {"hit": False}

        def upstream(request: httpx.Request) -> httpx.Response:
            called["hit"] = True   # 预算拦截应发生在转发前，不该触达上游
            return httpx.Response(200, json={"ok": True})

        llm_proxy.set_forward_client_for_testing(
            httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        )
        token = llm_proxy.token_registry.issue("sess-cap", "u-cap")
        try:
            async with _app_client() as c:
                r = await c.post(
                    "/internal/llm/chat/completions",
                    headers={"authorization": f"Bearer {token}"},
                    json={"model": "x", "messages": []},
                )
                return r.status_code, called["hit"]
        finally:
            llm_proxy.token_registry.revoke_for_session("sess-cap")
            llm_proxy.reset_user_budget_store_for_testing(None)
    code, hit = asyncio.run(go())
    assert code == 429
    assert hit is False


def test_non_streaming_response_still_counts_tokens():
    """StreamConsumed 分支（非流式 / 即时内容响应）也要计量 token，不得绕过预算。

    上游桩返回纯 JSON（无 data: 前缀）：若走普通逐行扫描分支会因不匹配 'data: ' 而
    漏计（best_total_tokens=0），只有 StreamConsumed 分支用 _extract_total_tokens
    才能提取。本用例验证用户日预算与会话累计都被正确累加。
    """
    async def go():
        def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [], "usage": {"total_tokens": 321}})

        llm_proxy.set_forward_client_for_testing(
            httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        )
        object.__setattr__(settings, "llm_api_key", "REALKEY")
        object.__setattr__(settings, "user_daily_token_limit", 500000)
        store = llm_proxy.MemoryUserBudgetStore()
        llm_proxy.reset_user_budget_store_for_testing(store)
        llm_proxy._session_tokens.clear()
        token = llm_proxy.token_registry.issue("sess-count", "u-count")
        try:
            async with _app_client() as c:
                r = await c.post(
                    "/internal/llm/chat/completions",
                    headers={"authorization": f"Bearer {token}"},
                    json={"model": "x", "messages": []},
                )
                _ = r.text  # 消费整个流，确保 _relay_and_count 的 finally 完成累加
            return (
                store._tokens.get("u-count", {}).get("tokens"),
                llm_proxy._session_tokens.get("sess-count"),
            )
        finally:
            llm_proxy.token_registry.revoke_for_session("sess-count")
            llm_proxy.reset_user_budget_store_for_testing(None)
            llm_proxy._session_tokens.clear()
    user_tokens, session_tokens = asyncio.run(go())
    assert user_tokens == 321
    assert session_tokens == 321


# ── 多实例：Redis 预算 store + 单/多实例切换 ─────────────────────────────────

class _FakeAsyncRedis:
    """最小 async Redis 桩：只实现预算 store 用到的 get/incrby/expire。"""
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def incrby(self, key, amount):
        self.store[key] = int(self.store.get(key, 0)) + amount
        return self.store[key]

    async def expire(self, key, ttl):
        self.ttl[key] = ttl


def test_redis_user_budget_store_accumulates_and_sets_ttl():
    async def go():
        r = _FakeAsyncRedis()
        s = llm_proxy.RedisUserBudgetStore(r)
        first = await s.check("u", 100)   # 无记录 → 放行
        await s.add("u", 40)
        await s.add("u", 40)              # 累计 80
        under = await s.check("u", 100)   # 80 < 100
        await s.add("u", 30)              # 累计 110
        over = await s.check("u", 100)    # 110 >= 100 → 拦截
        key = f"srun:token:u:{llm_proxy._utc_today()}"
        return first, under, over, r.store[key], r.ttl.get(key)
    first, under, over, total, ttl = asyncio.run(go())
    assert first is True
    assert under is True
    assert over is False
    assert total == 110
    assert ttl is not None   # 当日首次写入即设 TTL，跨日自动清理


def test_budget_store_switch_single_vs_multi_instance():
    """单实例→内存实现；多实例但未配 Redis→fail-fast，保证切换靠配置。"""
    saved_mi = settings.multi_instance
    saved_host = settings.redis_host
    try:
        # 单实例：进程内存
        llm_proxy.reset_user_budget_store_for_testing(None)
        object.__setattr__(settings, "multi_instance", False)
        assert isinstance(llm_proxy.get_user_budget_store(), llm_proxy.MemoryUserBudgetStore)

        # 多实例但没配 Redis：必须 fail-fast，不静默退回内存
        llm_proxy.reset_user_budget_store_for_testing(None)
        object.__setattr__(settings, "multi_instance", True)
        object.__setattr__(settings, "redis_host", "")
        raised = False
        try:
            llm_proxy.get_user_budget_store()
        except RuntimeError:
            raised = True
        assert raised
    finally:
        object.__setattr__(settings, "multi_instance", saved_mi)
        object.__setattr__(settings, "redis_host", saved_host)
        llm_proxy.reset_user_budget_store_for_testing(None)
