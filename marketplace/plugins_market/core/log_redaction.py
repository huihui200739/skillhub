# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""日志敏感信息脱敏：URL query 中的 token / secret 等。"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# query 参数名（不区分大小写）
_SENSITIVE_QUERY_PARAM = re.compile(
    r"([?&](?:access_token|token|password|client_secret|api_key|secret|refresh_token)=)([^&\s\"']+)",
    re.IGNORECASE,
)

# Bearer token in headers-like log fragments
_BEARER_TOKEN = re.compile(r"(Bearer\s+)([^\s\"']+)", re.IGNORECASE)

_REDACTION_CONFIGURED = False


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    out = _SENSITIVE_QUERY_PARAM.sub(r"\1***", text)
    return _BEARER_TOKEN.sub(r"\1***", out)


class SensitiveLogFilter(logging.Filter):
    """Attach to loggers to scrub tokens from log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_sensitive_text(record.msg)
            if record.args:
                record.args = tuple(
                    redact_sensitive_text(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        except Exception as exc:
            _log.debug("sensitive log redaction failed: %s", exc, exc_info=True)
        return True


def configure_sensitive_log_redaction() -> None:
    """为 httpx / interface / root 日志挂载脱敏；httpx 降为 WARNING。"""
    global _REDACTION_CONFIGURED
    if _REDACTION_CONFIGURED:
        return
    _REDACTION_CONFIGURED = True

    filt = SensitiveLogFilter()
    for name in ("httpx", "httpcore", "interface", ""):
        logger = logging.getLogger(name)
        logger.addFilter(filt)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
