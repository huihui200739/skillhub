# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Worker Pod 预热池：warm pod 跨 session 复用。

复用的是"温度"（pod / openjiuwen import）——每个 session 仍由 worker 建/删全新 agent 和 workspace。
token 为 per-pod 常驻：worker_token（worker 端点鉴权）+ llm_token（LLM 代理鉴权），
warm 创建时写进 pod env；control 面在 acquire 时把 llm_token 绑到当前 session、release 时解绑。
"""
from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..config import settings
from .pod_manager import PodManager

_log = logging.getLogger(__name__)

# (worker_token, llm_token) -> pod env（通用 LLM 配置 + 两个 per-pod token，无 session 专属）
EnvBuilder = Callable[[str, str], dict]


@dataclass
class PodLease:
    name: str
    ip: str
    worker_token: str
    llm_token: str

    @property
    def endpoint(self) -> str:
        return f"{self.ip}:{settings.pod_port}"


class PodPoolManager:
    """空闲 warm pod 池。控制面单副本、asyncio 单线程事件循环下无需加锁。"""

    def __init__(
        self,
        pod_manager: PodManager,
        env_builder: EnvBuilder,
        pool_size: int,
        max_idle: int,
    ) -> None:
        self._pods = pod_manager
        self._env_builder = env_builder
        self._pool_size = pool_size
        self._max_idle = max(max_idle, pool_size)
        self._idle: list[PodLease] = []
        # 探活复用同一个 client，避免每次 acquire 都新建连接池
        self._probe_client: httpx.AsyncClient | None = None

    def idle_count(self) -> int:
        return len(self._idle)

    async def _spawn(self) -> PodLease:
        worker_token = secrets.token_urlsafe(32)
        llm_token = "pod-" + secrets.token_urlsafe(24)
        env = self._env_builder(worker_token, llm_token)
        pod_id = f"pool-{uuid.uuid4().hex[:10]}"
        name = await self._pods.create_pod(pod_id, env)
        ip = await self._pods.wait_ready(name, settings.pod_ready_timeout_seconds)
        return PodLease(name=name, ip=ip, worker_token=worker_token, llm_token=llm_token)

    async def warm(self) -> None:
        """补到 pool_size 个空闲 warm pod。"""
        while len(self._idle) < self._pool_size:
            self._idle.append(await self._spawn())

    async def _is_alive(self, lease: PodLease, timeout: float = 2.0) -> bool:
        """轻量探活：acquire 前剔除死 lease（pod 被外部删 / OOM / 集群重启等场景，
        内存 idle 里残留死引用）。复用 _probe_client。"""
        if self._probe_client is None:
            self._probe_client = httpx.AsyncClient(timeout=timeout)
        try:
            resp = await self._probe_client.get(f"http://{lease.endpoint}/healthz")
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    async def acquire(self) -> PodLease:
        """取一个 warm pod；池空或 idle pod 已死则按需新建。

        死 lease 自愈：pop 出来先探活，活的返回，死的丢弃 + best-effort 删 pod。
        """
        while self._idle:
            lease = self._idle.pop()
            if await self._is_alive(lease):
                return lease
            _log.warning(
                "evicting dead lease from pool: name=%s ip=%s endpoint=%s",
                lease.name, lease.ip, lease.endpoint,
            )
            try:
                await self._pods.delete_pod(lease.name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("best-effort delete of dead pod %s failed: %s", lease.name, exc)
        return await self._spawn()

    async def release(self, lease: PodLease, *, healthy: bool = True) -> None:
        """归还：健康且未超 max_idle 则回池，否则销毁。"""
        if healthy and len(self._idle) < self._max_idle:
            self._idle.append(lease)
            return
        await self._pods.delete_pod(lease.name)

    async def shutdown(self) -> None:
        leases, self._idle = self._idle[:], []
        for lease in leases:
            await self._pods.delete_pod(lease.name)
        if self._probe_client is not None:
            await self._probe_client.aclose()
            self._probe_client = None
