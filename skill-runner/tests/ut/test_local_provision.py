# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LocalExecutor._provision_skill 单元测试：ZIP 解包与 strip prefix 选取。

运行：
    pytest skill-runner/tests/ut/test_local_provision.py
    python  skill-runner/tests/ut/test_local_provision.py
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import zipfile

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

from skill_runner.executor.local import LocalExecutor
from skill_runner.models import Session
from skill_runner.skill_loader import SkillBundle


def _bundle_from_entries(entries: list[tuple[str, bytes]]) -> SkillBundle:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return SkillBundle(
        asset_id="s", version="", skill_md="", workflow_md="",
        roles={}, team_mode="", package_bytes=buf.getvalue(),
    )


async def _provision(bundle: SkillBundle) -> tuple[str, str]:
    """跑一次 _provision_skill，返回 (临时目录不删的 skill_dir 快照, 内容)。"""
    session = Session(skill_id="s", version="", workspace="", skill_type="ordinary")
    session.extra["skill_bundle"] = bundle
    tmp = tempfile.mkdtemp(prefix="test_provision_")
    skill_dir = os.path.join(tmp, "skill")
    os.makedirs(skill_dir)
    await LocalExecutor()._provision_skill(session, skill_dir)
    return skill_dir


async def main() -> int:
    fails: list[str] = []

    # 层级最浅的 SKILL.md 定 strip prefix：docs/SKILL.md 排在顶层 SKILL.md 之前，
    # 不能被它带偏把整包裁成 docs/ 前缀。
    skill_dir = await _provision(_bundle_from_entries([
        ("myskill/docs/SKILL.md", b"deep"),
        ("myskill/SKILL.md", b"root"),
        ("myskill/tool.py", b"print()"),
    ]))
    for rel in ("SKILL.md", "tool.py", os.path.join("docs", "SKILL.md")):
        if not os.path.isfile(os.path.join(skill_dir, rel)):
            fails.append(f"shallowest prefix: missing {rel} after extract")
    root_md = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(root_md) and open(root_md, "rb").read() != b"root":
        fails.append("shallowest prefix: top-level SKILL.md content wrong")

    # 顶层就是 skill 根（无外层目录）：prefix 为空，文件原样落地。
    skill_dir2 = await _provision(_bundle_from_entries([
        ("SKILL.md", b"flat"),
        ("tool.py", b"x"),
    ]))
    if not os.path.isfile(os.path.join(skill_dir2, "SKILL.md")):
        fails.append("flat package: SKILL.md should extract at root")

    # ZipSlip 防护：带 ../ 的成员不得逃出 skill_dir。
    skill_dir3 = await _provision(_bundle_from_entries([
        ("myskill/SKILL.md", b"ok"),
        ("myskill/../evil.txt", b"pwned"),
    ]))
    if os.path.isfile(os.path.join(os.path.dirname(skill_dir3), "evil.txt")):
        fails.append("zipslip: ../evil.txt escaped skill dir")

    if fails:
        logger.info("RESULT: FAIL")
        for f in fails:
            logger.info("  - %s", f)
        return 1
    logger.info("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


def test_local_provision() -> None:
    """pytest 入口。"""
    rc = asyncio.run(main())
    assert rc == 0, f"test_local_provision self-check failed (rc={rc})"
