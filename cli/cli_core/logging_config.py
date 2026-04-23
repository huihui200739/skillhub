import logging
import re
import sys

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


# Avoid a generic "\\btoken\\b" pattern: it false-positives on prose like "revoke token: see docs".
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(authorization)\b\s*[:=]\s*([^\s]+)"),
    re.compile(r"(?i)\b(x-system-token)\b\s*[:=]\s*([^\s]+)"),
    re.compile(
        r"(?i)\b(access_token|refresh_token|id_token|user_token)\b\s*[:=]\s*([^\s]+)"
    ),
)


def _redact_secrets(text: str) -> str:
    redacted = text
    for pat in _SECRET_PATTERNS:
        redacted = pat.sub(lambda m: f"{m.group(1)}=<redacted>", redacted)
    return redacted


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Only touch the rendered message; avoid mutating args structure in a surprising way.
        try:
            msg = record.getMessage()
        except Exception:
            return True
        new_msg = _redact_secrets(str(msg))
        if new_msg != msg:
            record.msg = new_msg
            record.args = ()
        return True


def setup_logging(
    level: str = "INFO",
    log_format: str | None = None,
    date_format: str | None = None,
) -> None:
    """Configure root logging (level and console handler); call once at CLI entry."""
    global _initialized
    if _initialized:
        return

    fmt = log_format or _DEFAULT_FORMAT
    date_fmt = date_format or _DATE_FORMAT
    level_value = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level_value)

    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level_value)
        handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (standard ``logging.getLogger``)."""
    return logging.getLogger(name)
