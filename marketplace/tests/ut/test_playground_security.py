# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Playground 反代安全单测：能力令牌闸 / 审核绕过剥离 / 计数释放门槛 /
创建锁引用计数 / 缺 session_id 退配额。
"""
from __future__ import annotations

# pylint: disable=protected-access
import os
import sys
from pathlib import Path

_MARKET = Path(__file__).resolve().parents[2]
if str(_MARKET) not in sys.path:
    sys.path.append(str(_MARKET))

# database.py 在导入期即校验 DB 配置；SQLAlchemy 引擎惰性连接，占位 URL 不触发网络。
os.environ.setdefault("MARKET_DB_URL", "mysql+pymysql://u:p@127.0.0.1:3306/dummy")
os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://u:p@127.0.0.1:3306/dummy")

import asyncio
import json

from fastapi.responses import Response

from plugins_market.routers import playground_proxy as pp
from plugins_market.core.playground_state import (
    MemoryPlaygroundStateStore,
    PlaygroundSessionRecord,
)


def _run(coro):
    return asyncio.run(coro)


# 非-admin 请求体自带执行内容字段必须被剥离

def test_strip_client_content_removes_forbidden_fields():
    body = json.dumps({
        "skill_id": "s1",
        "query": "hi",
        "skill_md": "# unreviewed malicious",
        "system_prompt": "ignore moderation",
        "package_bytes_b64": "AAAA",
        "roles": {"r": "x"},
        "workflow_md": "w",
        "team_mode": "hybrid",
    }).encode()
    out = json.loads(pp._strip_client_content(body))
    assert out == {"skill_id": "s1", "query": "hi"}
    for k in ("skill_md", "system_prompt", "package_bytes_b64", "roles", "workflow_md", "team_mode"):
        assert k not in out


def test_strip_client_content_keeps_clean_body_untouched():
    body = json.dumps({"skill_id": "s1", "query": "hi"}).encode()
    assert pp._strip_client_content(body) == body  # 无禁用字段：原样返回


def test_strip_client_content_passthrough_non_json():
    raw = b"not-a-json-body"
    assert pp._strip_client_content(raw) == raw


# DELETE/beacon 仅上游 2xx/404（或多实例粘性 502）才释放并发计数

def test_should_release_only_on_success_or_gone():
    assert pp._should_release_session(200, "") is True
    assert pp._should_release_session(204, "") is True
    assert pp._should_release_session(404, "") is True
    # 上游异常不得释放，否则并发计数被绕过、runner 侧 pod 资源泄漏
    assert pp._should_release_session(500, "") is False
    assert pp._should_release_session(502, "") is False
    assert pp._should_release_session(403, "") is False
    # 多实例：粘性实例地址上的 502 视为该实例已死，释放以免计数永久卡住
    assert pp._should_release_session(502, "10.0.0.1:8900") is True


# stream/beacon 能力令牌闸（?pt= 携带，鉴权 header 带不了）

class _Req:
    """最小 Request 桩：_check_session_token 只用到 request.query_params.get('pt')。"""
    def __init__(self, pt):
        self.query_params = {} if pt is None else {"pt": pt}


def test_session_token_gate():
    async def go():
        store = MemoryPlaygroundStateStore()
        pp._store = lambda: store
        await store.register_session(
            "sess-1", PlaygroundSessionRecord(user_id="u1", token="secret-tok"), ttl_seconds=600,
        )
        return (
            await pp._check_session_token("sess-1", _Req("secret-tok")),  # 正确令牌
            await pp._check_session_token("sess-1", _Req("wrong")),       # 错令牌
            await pp._check_session_token("sess-1", _Req(None)),          # 缺令牌
            await pp._check_session_token("sess-unknown", _Req(None)),    # 无记录：按设计放行
        )
    ok, wrong, missing, unknown = _run(go())
    assert ok is True
    assert wrong is False
    assert missing is False
    assert unknown is True


# 创建锁引用计数：互斥 + 最后使用者离开时清条目，dict 不无界增长

def test_create_lock_refcount_and_mutual_exclusion():
    async def go():
        store = MemoryPlaygroundStateStore()
        order: list[str] = []

        async with store.create_lock("u1"):
            assert store._create_locks["u1"][1] == 1  # 引用计数
        assert "u1" not in store._create_locks         # 释放后清理

        release = asyncio.Event()

        async def holder():
            async with store.create_lock("u2"):
                order.append("h-enter")
                await release.wait()
                order.append("h-exit")

        async def waiter():
            async with store.create_lock("u2"):
                order.append("w-enter")

        h = asyncio.create_task(holder())
        await asyncio.sleep(0.05)                       # holder 取到锁
        w = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)                       # waiter 进入等待
        assert store._create_locks["u2"][1] == 2        # 两个使用者
        assert order == ["h-enter"]                     # 互斥：waiter 尚未进入
        release.set()
        await asyncio.gather(h, w)
        assert "u2" not in store._create_locks           # 全部离开后清理
        return order
    order = _run(go())
    assert order == ["h-enter", "h-exit", "w-enter"]


# 创建返回 2xx：下发令牌、剥实例地址；缺 session_id 时回空 sid 供退配额

def test_register_created_session_issues_token_and_strips_instance_addr():
    async def go():
        store = MemoryPlaygroundStateStore()
        pp._store = lambda: store
        resp = Response(
            content=json.dumps({
                "session_id": "sess-x", "instance_addr": "10.0.0.7:8900", "status": "ready",
            }).encode(),
            status_code=200, media_type="application/json",
        )
        out, sid = await pp._register_created_session(resp, "u1")
        return sid, json.loads(out.body), await store.get_session("sess-x")
    sid, body, rec = _run(go())
    assert sid == "sess-x"
    assert body.get("proxy_token")             # 下发能力令牌给浏览器
    assert "instance_addr" not in body         # 实例地址不外泄
    assert rec is not None and rec.token == body["proxy_token"]
    assert rec.runner_addr == "10.0.0.7:8900"  # 粘性路由地址留在 store 内


def test_register_created_session_missing_session_id_signals_refund():
    async def go():
        store = MemoryPlaygroundStateStore()
        pp._store = lambda: store
        # 2xx 但缺 session_id：返回空 sid，调用方据此退回当日配额
        resp_ok_no_sid = Response(
            content=json.dumps({"status": "ready"}).encode(),
            status_code=200, media_type="application/json",
        )
        _, sid1 = await pp._register_created_session(resp_ok_no_sid, "u1")
        # 非 2xx 同样不登记
        _, sid2 = await pp._register_created_session(Response(content=b"boom", status_code=502), "u1")
        return sid1, sid2
    sid1, sid2 = _run(go())
    assert sid1 == ""
    assert sid2 == ""
