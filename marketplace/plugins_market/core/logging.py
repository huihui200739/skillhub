# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging
import os
import sys
from collections.abc import Mapping
from functools import wraps
from typing import Any, Callable, TypeVar, cast

import structlog
from structlog.contextvars import merge_contextvars

from plugins_market.core.context import get_request_id, get_duration_ms
from plugins_market.core.operation_log import (
    get_operation_id,
    get_operation_type,
    get_parent_operation_id,
)

F = TypeVar("F", bound=Callable[..., Any])


def _render_plain_log(event_dict: dict[str, Any]) -> str:
    ts = event_dict.pop("timestamp", "")
    level = str(event_dict.pop("level", "")).upper()
    logger_name = event_dict.pop("logger", "")
    event = str(event_dict.pop("event", ""))

    parts = [part for part in (ts, level, logger_name) if part]
    rendered = ", ".join(parts)

    if event:
        rendered = f"{rendered}, event={event}" if rendered else f"event={event}"

    if event_dict:
        rendered_fields = [f"{key}={value}" for key, value in event_dict.items()]
        suffix = ", ".join(rendered_fields)
        rendered = f"{rendered}, {suffix}" if rendered else suffix

    return rendered


def setup_logging(debug: bool = False):
    shared_processors = [
        merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if debug:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    level = logging.DEBUG if debug else logging.INFO

    class PlainLogFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            msg = record.msg
            if isinstance(msg, Mapping):
                return _render_plain_log(dict(msg))
            if isinstance(msg, str):
                return record.getMessage()
            return super().format(record)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(PlainLogFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.setLevel(level)
    root_logger.propagate = False

    def inject_request_context(logger, method_name, event_dict):
        request_id = get_request_id()
        if request_id:
            event_dict["request_id"] = request_id

        operation_id = get_operation_id()
        if operation_id:
            event_dict["operation_id"] = operation_id

        operation_type = get_operation_type()
        if operation_type:
            event_dict["operation_type"] = operation_type

        parent_operation_id = get_parent_operation_id()
        if parent_operation_id:
            event_dict["parent_operation_id"] = parent_operation_id

        duration_ms = get_duration_ms()
        if duration_ms > 0:
            event_dict["duration_ms"] = duration_ms

        return event_dict

    def render_plain_log(logger, method_name, event_dict):
        return _render_plain_log(event_dict)

    structlog.configure(
        processors=[inject_request_context] + shared_processors + [render_plain_log],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    from plugins_market.core.interface_log import setup_interface_logger
    log_dir = os.getenv("INTERFACE_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "interface.log")
    setup_interface_logger(log_file=log_file)

    from plugins_market.core.log_redaction import configure_sensitive_log_redaction

    configure_sensitive_log_redaction()


def get_logger(name: str = None):
    """Get a structured logger."""
    return structlog.get_logger(name or __name__)


def non_request_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                extra = context_extractor(*args, **kwargs) if context_extractor else {}
                logger.exception(message, error_message=str(exc), **extra)
                if reraise:
                    raise
                return None

        return cast(F, wrapper)

    return decorator


def non_request_async_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any):
            try:
                return await func(*args, **kwargs)
            except Exception:
                extra = context_extractor(*args, **kwargs) if context_extractor else {}
                logger.exception(message, **extra)
                if reraise:
                    raise
                return None

        return cast(F, wrapper)

    return decorator


def background_task_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    return non_request_exception_boundary(
        logger=logger,
        message=message,
        reraise=reraise,
        context_extractor=context_extractor,
    )


def startup_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    return non_request_exception_boundary(
        logger=logger,
        message=message,
        reraise=reraise,
        context_extractor=context_extractor,
    )
