# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 源全局去重键：规范化仓库 + 分支/tag + skills_subpath → SHA256 hex（与迁移 SQL 一致）。"""

from __future__ import annotations

import hashlib
from typing import Optional

# 与 MySQL 迁移中 CHAR(31) 一致
_SEP = "\x1f"


def compute_git_source_dedup_key(
    *,
    repo_url_canonical: str,
    ref: str,
    skills_subpath: Optional[str],
) -> str:
    c = (repo_url_canonical or "").strip()
    r = (ref or "").strip()
    s = (skills_subpath or "").strip()
    raw = f"{c}{_SEP}{r}{_SEP}{s}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
