# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local sliding-window rate limiter for anonymous/public endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Per-key sliding window counter (thread-safe within one process)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_sec: float = 60.0) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - window_sec
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


_clawhub_compat_limiter = SlidingWindowRateLimiter()


def check_clawhub_compat_rate_limit(client_key: str, *, limit_per_minute: int) -> bool:
    """Return True if the request is allowed under the configured per-minute limit."""
    return _clawhub_compat_limiter.allow(
        f"clawhub:{client_key}",
        limit=limit_per_minute,
        window_sec=60.0,
    )
