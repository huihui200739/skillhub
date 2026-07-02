# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""K8sPodManager 孤儿回收单测：清理 Failed/Succeeded 及超寿命仍 Running 的 pod。

    pytest skill-runner/tests/ut/test_pod_reaper.py -q
"""
from __future__ import annotations

# pylint: disable=protected-access
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from skill_runner.config import settings
from skill_runner.executor.pod_manager import K8sPodManager


class _FakeCore:
    def __init__(self, pods) -> None:
        self._pods = pods
        self.deleted: list[str] = []

    async def list_namespaced_pod(self, namespace, label_selector):
        return SimpleNamespace(items=self._pods)

    async def delete_namespaced_pod(self, name, namespace):
        self.deleted.append(name)


def _pod(name: str, phase: str, age_seconds: float = 0):
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return SimpleNamespace(
        status=SimpleNamespace(phase=phase),
        metadata=SimpleNamespace(name=name, creation_timestamp=created),
    )


def test_reap_finished_and_orphan_pods():
    async def go():
        orphan_age = settings.session_max_lifetime_seconds + settings.pod_orphan_grace_seconds
        pods = [
            _pod("failed-1", "Failed"),
            _pod("succeeded-1", "Succeeded"),
            _pod("running-young", "Running", age_seconds=5),
            _pod("running-orphan", "Running", age_seconds=orphan_age + 60),
            _pod("pending-1", "Pending"),
        ]
        pm = K8sPodManager()
        fake = _FakeCore(pods)
        pm._core = fake  # 短路 _api()，免连真实集群
        reaped = await pm.reap_finished_pods()
        return reaped, sorted(fake.deleted)
    reaped, deleted = asyncio.run(go())
    # Failed/Succeeded 直接删；Running 仅超寿命的孤儿删；年轻 Running 与 Pending 保留
    assert deleted == ["failed-1", "running-orphan", "succeeded-1"]
    assert reaped == 3
