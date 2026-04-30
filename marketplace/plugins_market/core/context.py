# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import random
import string
import time
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from typing import Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
start_time_var: ContextVar[Optional[datetime]] = ContextVar("start_time", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)

_BJ_TZ = timezone(timedelta(hours=8))


def generate_request_id() -> str:
    now_ms = int(time.time() * 1000)
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"req_{now_ms}_{rand_chars}"


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def get_start_time() -> Optional[datetime]:
    return start_time_var.get()


def set_user_id(user_id: str) -> None:
    user_id_var.set(user_id)


def get_user_id() -> str:
    return user_id_var.get() or "anonymous"


def set_request_context(request_id: Optional[str] = None) -> str:
    if not request_id:
        request_id = generate_request_id()
    
    request_id_var.set(request_id)
    start_time_var.set(datetime.now(timezone.utc))
    return request_id


def clear_request_context() -> None:
    request_id_var.set(None)
    start_time_var.set(None)
    user_id_var.set(None)


def get_duration_ms() -> int:
    start_time = get_start_time()
    if not start_time:
        return 0
    delta = datetime.now(timezone.utc) - start_time
    return int(delta.total_seconds() * 1000)


def now_bj_iso() -> str:
    now = datetime.now(_BJ_TZ)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}+08:00"
