# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class FinderTraceEvent:
    event_type: str
    node_id: str
    depth: int
    detail: Dict[str, object] = field(default_factory=dict)


@dataclass
class FinderTrace:
    events: List[FinderTraceEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, event_type: str, *, node_id: str, depth: int, detail: Dict[str, object] | None = None) -> None:
        payload = dict(detail or {})
        with self._lock:
            self.events.append(FinderTraceEvent(event_type=event_type, node_id=node_id, depth=depth, detail=payload))


__all__ = ["FinderTrace", "FinderTraceEvent"]
