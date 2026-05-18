# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 接入 ref 规则：填写分支名或 tag；仅拦截「完整 40 位 SHA-1」形态的 commit（与合法分支名区分）。"""

from __future__ import annotations

import re

from plugins_market.core.errors import PublishError

# 仅拦截标准 Git SHA-1 对象名（40 位十六进制）。短 SHA、含字母的非十六进制分支名均不拦截，避免误伤如 deadbeef、cafebabe 等分支。
_FULL_SHA1_HEX = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def assert_git_ref_branch_or_tag(ref: str) -> str:
    r = (ref or "").strip()
    if not r:
        raise PublishError(code=400, error="invalid_git_ref", message="请填写分支名或 tag。")
    if r.startswith("-"):
        raise PublishError(code=400, error="invalid_git_ref", message="ref 不能以 - 开头（避免被 git 解析为选项）。")
    if ".." in r or "\x00" in r:
        raise PublishError(code=400, error="invalid_git_ref", message="ref 包含非法字符。")
    if _FULL_SHA1_HEX.match(r):
        raise PublishError(
            code=400,
            error="invalid_git_ref",
            message="不支持以完整 40 位 commit SHA 作为拉取目标，请填写分支名或 tag。",
        )
    if len(r) > 256:
        raise PublishError(code=400, error="invalid_git_ref", message="ref 过长。")
    return r
