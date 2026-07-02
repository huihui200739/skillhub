# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PodPoolManager 单元测试：池本身的 spawn / max_idle / unhealthy / shutdown 边界。"""
from __future__ import annotations

import asyncio
import logging
import time

from skill_runner.config import settings
from skill_runner.executor.pod_manager import PodManager

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)
from skill_runner.executor.pod_pool import PodPoolManager, PodLease


class AlwaysAlivePool(PodPoolManager):
    """探活恒真的池，供池管理单测使用。

    真实探活会 httpx GET“10.0.0.x:8080/healthz”，fake pod manager 给的是假 IP、
    必然探不通，lease 会被当成 dead 全清掉。这里不在乎探活本身，覆写为始终存活。
    """

    async def _is_alive(self, lease: PodLease, timeout: float = 2.0) -> bool:
        return True


class FakePM(PodManager):
    def __init__(self, *, spawn_fails: bool = False) -> None:
        self.created: dict[str, dict] = {}
        self.deleted: list[str] = []
        self._n = 0
        self._spawn_fails = spawn_fails

    async def create_pod(self, pod_id: str, env: dict[str, str]) -> str:
        if self._spawn_fails:
            raise RuntimeError("k8s api unreachable")
        self._n += 1
        self.created[pod_id] = env
        return f"{pod_id}-{self._n}"

    async def wait_ready(self, name: str, timeout: float) -> str:
        return f"10.0.0.{self._n}"

    async def delete_pod(self, name: str) -> None:
        self.deleted.append(name)


def _env_builder(worker_token: str, llm_token: str) -> dict:
    return {"WORKER_AUTH_TOKEN": worker_token, "LLM_API_KEY": llm_token}


async def main() -> int:
    pm = FakePM()
    pool = AlwaysAlivePool(pm, _env_builder, pool_size=2, max_idle=2)
    fails: list[str] = []

    # 池空 + on-demand spawn：未 warm，acquire 即按需新建一个
    a = await pool.acquire()
    if len(pm.created) != 1:
        fails.append(f"on-demand acquire (empty pool): expected 1 created, got {len(pm.created)}")
    if not a.endpoint.startswith("10.0.0."):
        fails.append(f"lease endpoint wrong: {a.endpoint}")

    # max_idle 上限：先把池灌满，再归还一个超额 lease → 应触发删除而非回池
    await pool.warm()  # 补到 pool_size=2
    if pool.idle_count() != 2:
        fails.append(f"warm to pool_size: idle expected 2, got {pool.idle_count()}")
    # a 是池外多出来的 lease，归还应触发删除
    await pool.release(a, healthy=True)
    if pool.idle_count() != 2:
        fails.append(f"release over max_idle: idle should stay 2, got {pool.idle_count()}")
    if a.name not in pm.deleted:
        fails.append("over-idle release should delete the pod")

    # 不健康归还 → 直接删，不入池
    d = await pool.acquire()
    await pool.release(d, healthy=False)
    if d.name not in pm.deleted:
        fails.append("unhealthy release should delete the pod")

    # shutdown 清空 idle
    before = len(pm.deleted)
    await pool.shutdown()
    if pool.idle_count() != 0:
        fails.append("shutdown should empty idle")
    if len(pm.deleted) <= before:
        fails.append("shutdown should delete idle pods")

    # 剩余寿命不足的 lease：acquire 弃用换新，避免会话中途被 K8s 强杀
    pm2 = FakePM()
    pool2 = AlwaysAlivePool(pm2, _env_builder, pool_size=1, max_idle=2)
    aged = PodLease(
        name="aged-pod", ip="10.0.0.9", worker_token="w", llm_token="l",
        created_at=time.monotonic() - (settings.session_max_lifetime_seconds
                                       - settings.pool_min_lease_lifetime_seconds + 60),
    )
    pool2._idle.append(aged)
    lease = await pool2.acquire()
    if lease.name == "aged-pod":
        fails.append("aged lease: should be discarded, not returned")
    if "aged-pod" not in pm2.deleted:
        fails.append("aged lease: discarded pod should be deleted")

    # 空池 + spawn 失败：acquire 抛异常向上传，绝不无限阻塞
    pm3 = FakePM(spawn_fails=True)
    pool3 = AlwaysAlivePool(pm3, _env_builder, pool_size=1, max_idle=1)
    try:
        await asyncio.wait_for(pool3.acquire(timeout=5), timeout=3)
        fails.append("acquire w/ spawn failure: expected RuntimeError, returned")
    except RuntimeError:
        pass
    except asyncio.TimeoutError:
        fails.append("acquire w/ spawn failure: hung instead of raising")

    if fails:
        logger.info("RESULT: FAIL")
        for f in fails:
            logger.info("  - %s", f)
        return 1
    logger.info("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


def test_pod_pool() -> None:
    """pytest 入口：跳 main() 设为同步函数供 pytest 发现。"""
    rc = asyncio.run(main())
    assert rc == 0, f"test_pod_pool self-check failed (rc={rc})"
