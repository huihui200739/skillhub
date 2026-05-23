# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging
import re

from starlette.types import ASGIApp, Receive, Scope, Send

from plugins_market.core.config import settings
from plugins_market.core.context import (
    set_request_context,
    clear_request_context,
    get_request_id,
    get_duration_ms,
    get_user_id,
    set_source_channel,
    detect_source_channel_from_ua,
)
from plugins_market.core.interface_log import log_interface, determine_success, InterfaceLogParams

logger = logging.getLogger(__name__)

_EXCLUDE_PATHS = {"/api/health", "/favicon.ico", "/docs", "/redoc", "/openapi.json"}

# audit_logs.request_id 是 VARCHAR(64)；外部传入需校验长度 + 字符集，否则丢弃改为生成新的
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _get_client_ip(scope: dict) -> str:
    headers = dict(scope.get("headers", []))
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",")[0].strip()
    real_ip = headers.get(b"x-real-ip")
    if real_ip:
        return real_ip.decode("latin-1").strip()
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


def _get_request_id_from_scope(scope: dict) -> str:
    headers = dict(scope.get("headers", []))
    for key in (b"x-request-id", b"x-trace-id"):
        val = headers.get(key)
        if val:
            decoded = val.decode("latin-1", errors="ignore").strip()
            if _REQUEST_ID_RE.match(decoded):
                return decoded
    return set_request_context()


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        interface_name = f"{method} {path}"
        source_ip = _get_client_ip(scope)

        request_id = _get_request_id_from_scope(scope)
        set_request_context(request_id)

        # source_channel：根据 UA 推断；后续 audit_log 调用会自动带上
        headers_dict = dict(scope.get("headers", []))
        ua_header = headers_dict.get(b"user-agent")
        ua_str = ua_header.decode("latin-1") if ua_header else ""
        set_source_channel(detect_source_channel_from_ua(ua_str))

        status_code = 200
        body_size = 0
        exc_occurred = False
        exc_info_str = ""

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, body_size
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                duration_ms = get_duration_ms()
                headers.append((b"x-response-time", f"{duration_ms}ms".encode("latin-1")))
                for h in headers:
                    if h[0] == b"content-length":
                        try:
                            body_size = int(h[1])
                        except ValueError:
                            pass
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            exc_occurred = True
            exc_info_str = str(exc)[:200]
            logger.error(
                "Request failed",
                extra={
                    "request_id": request_id,
                    "duration_ms": get_duration_ms(),
                    "path": path,
                    "method": method,
                    "error": exc_info_str,
                },
                exc_info=True,
            )
            raise
        finally:
            duration_ms = get_duration_ms()
            user_id = get_user_id()

            if settings.interface_log_enabled and path not in _EXCLUDE_PATHS:
                if exc_occurred:
                    log_interface(InterfaceLogParams(
                        request_id=request_id,
                        interface_name=interface_name,
                        source_ip=source_ip,
                        user_id=user_id,
                        cost_time=duration_ms,
                        success=False,
                        return_code="500",
                        return_info=exc_info_str,
                        body_size=0,
                    ))
                else:
                    success = determine_success(status_code)
                    return_info = "success" if success else f"http_{status_code}"
                    log_interface(InterfaceLogParams(
                        request_id=request_id,
                        interface_name=interface_name,
                        source_ip=source_ip,
                        user_id=user_id,
                        cost_time=duration_ms,
                        success=success,
                        return_code=str(status_code),
                        return_info=return_info,
                        body_size=body_size,
                    ))

            clear_request_context()
