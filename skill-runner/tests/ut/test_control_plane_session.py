# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""控制面会话并发单测：同一 session 上一轮未结束时拒新消息（409）；SSE 消费者
断连时本轮按超时中止而非永久挂死。

    pytest skill-runner/tests/ut/test_control_plane_session.py -q
"""
from __future__ import annotations

# pylint: disable=protected-access
import asyncio

import pytest
from fastapi import HTTPException

from skill_runner import app as cp
from skill_runner.config import settings
from skill_runner.models import Session, SessionStatus, SendMessageRequest, sse_event
from skill_runner.session_store import store


def _mk_session(status=SessionStatus.READY, queue_max: int = 16) -> Session:
    s = Session(skill_id="demo", version="v1", workspace="", skill_type="ordinary")
    s.status = status
    s.events = asyncio.Queue(maxsize=queue_max)
    return s


class _BlockingExecutor:
    """run_turn 卡在 release 上，模拟"上一轮仍在跑"。"""
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_turn(self, session, content):
        self.started.set()
        await self.release.wait()
        yield sse_event("answer", content="ok")


def test_second_message_while_turn_running_returns_409():
    async def go():
        sess = _mk_session()
        store._sessions[sess.session_id] = sess
        fake = _BlockingExecutor()
        cp._executor = fake
        cp._drive_tasks.pop(sess.session_id, None)
        drive = None
        try:
            # 第一条消息被接受，启动一轮（阻塞在 release，保持 running）
            r1 = await cp.send_message(sess.session_id, SendMessageRequest(content="a"))
            assert r1.message_id
            drive = cp._drive_tasks.get(sess.session_id)
            await asyncio.wait_for(fake.started.wait(), timeout=2)
            # 第二条消息：上一轮仍在跑 → 409，避免两个 turn 交错写同一 events 队列
            with pytest.raises(HTTPException) as ei:
                await cp.send_message(sess.session_id, SendMessageRequest(content="b"))
            return ei.value.status_code
        finally:
            fake.release.set()
            if drive is not None:
                try:
                    await asyncio.wait_for(drive, timeout=2)
                except Exception:
                    pass
            store._sessions.pop(sess.session_id, None)
            cp._drive_tasks.pop(sess.session_id, None)
            cp._executor = None
    assert asyncio.run(go()) == 409


def test_turn_aborts_when_sse_consumer_stalled():
    async def go():
        # 队列容量 1、无人消费：第一条入队后队列满，第二条 put 会阻塞
        sess = _mk_session(status=SessionStatus.ACTIVE, queue_max=1)
        store._sessions[sess.session_id] = sess

        class _TwoEvents:
            async def run_turn(self, session, content):
                yield sse_event("text", delta="1")
                yield sse_event("text", delta="2")

        cp._executor = _TwoEvents()
        saved = settings.sse_put_timeout_seconds
        object.__setattr__(settings, "sse_put_timeout_seconds", 0.2)
        try:
            # 消费者不取事件时 put 会阻塞；0.2s 超时后中止本轮，函数按时返回
            await asyncio.wait_for(cp._drive_turn(sess.session_id, "hi"), timeout=3)
            first = sess.events.get_nowait()
            return first.get("type"), first.get("delta"), sess.events.qsize()
        finally:
            object.__setattr__(settings, "sse_put_timeout_seconds", saved)
            store._sessions.pop(sess.session_id, None)
            cp._drive_tasks.pop(sess.session_id, None)
            cp._executor = None
    typ, delta, remaining = asyncio.run(go())
    assert typ == "text" and delta == "1"   # 仅第一条落队
    assert remaining == 0                     # 第二条被超时中止、done 因队列满丢弃
