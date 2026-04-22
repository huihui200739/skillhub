import logging
import os
import sys

import structlog
from structlog.contextvars import merge_contextvars

from plugins_market.core.context import get_request_id, get_duration_ms


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
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    def inject_request_context(logger, method_name, event_dict):
        request_id = get_request_id()
        if request_id:
            event_dict["request_id"] = request_id

        duration_ms = get_duration_ms()
        if duration_ms > 0:
            event_dict["duration_ms"] = duration_ms

        return event_dict

    structlog.configure(
        processors=[inject_request_context] + processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    from plugins_market.core.interface_log import setup_interface_logger
    log_dir = os.getenv("INTERFACE_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "interface.log")
    setup_interface_logger(log_file=log_file)


def get_logger(name: str = None):
    """Get a structured logger."""
    return structlog.get_logger(name or __name__)
