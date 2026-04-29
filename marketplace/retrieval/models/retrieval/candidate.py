# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FinderCandidate:
    rank: int
    item_id: str
    payload: str
    branch_path: tuple[str, ...]
    label: str = ""
    description: str = ""


__all__ = ["FinderCandidate"]
