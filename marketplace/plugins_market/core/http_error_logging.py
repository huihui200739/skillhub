# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import sys
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from plugins_market.core.audit_failed import audit_failed_mutation
from plugins_market.core.errors import (
    BusinessError,
    PublishError,
    as_json_response_body,
    ensure_standard_error_payload,
    internal_error_payload,
    normalize_http_exception_detail,
    validation_error_payload,
)
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    OperationContextSnapshot,
    failure_log_fields_from_payload,
    has_operation_completion_log,
    has_operation_context,
    is_invalid_or_denied_error,
    operation_log_fields,
    operation_log_fields_from_context,
)


_DEFAULT_LOGGER = get_logger("main")


def _resolved_logger(logger: Any | None) -> Any:
    if logger is not None:
        return logger
    main_module = sys.modules.get("main")
    module_logger = getattr(main_module, "logger", None) if main_module is not None else None
    return module_logger or _DEFAULT_LOGGER


def _emit_warning(logger: Any | None, event: str, **fields: Any) -> None:
    resolved = _resolved_logger(logger)
    log_warning = getattr(resolved, "warning", None)
    if callable(log_warning):
        log_warning(event, **fields)
        return
    log_info = getattr(resolved, "info", None)
    if callable(log_info):
        log_info(event, **fields)


def _emit_exception(logger: Any | None, event: str, *args: Any, **kwargs: Any) -> None:
    resolved = _resolved_logger(logger)
    log_exception = getattr(resolved, "exception", None)
    if callable(log_exception):
        log_exception(event, *args, **kwargs)
        return
    log_error = getattr(resolved, "error", None)
    if callable(log_error):
        log_error(event, **kwargs)


def _get_exception_operation_snapshot(exc: Exception | None) -> OperationContextSnapshot | None:
    snapshot = getattr(exc, "_operation_snapshot", None) if exc is not None else None
    if isinstance(snapshot, OperationContextSnapshot):
        return snapshot
    return None



def _log_exception_response(
    logger: Any | None,
    request: Request,
    *,
    status_code: int,
    payload: dict,
    stage: str = "complete",
    exc: Exception | None = None,
) -> None:
    if exc is not None and getattr(exc, "_operation_completion_logged", False):
        return
    snapshot = _get_exception_operation_snapshot(exc)
    snapshot_has_context = snapshot is not None and snapshot.context is not None
    if has_operation_context() and has_operation_completion_log():
        return
    if not has_operation_context() and snapshot is not None and snapshot.completion_logged:
        return
    result_fields = failure_log_fields_from_payload(payload)
    if not has_operation_context():
        if snapshot_has_context:
            result_fields = operation_log_fields_from_context(
                snapshot.context,
                stage=stage,
                result=result_fields.get("result") or "failure",
                error_code=payload.get("error_code"),
                error_class=payload.get("error_class"),
                error_message=payload.get("message"),
            )
        else:
            result_fields = operation_log_fields(
                stage=stage,
                result="failure",
                error_code=payload.get("error_code"),
                error_class=payload.get("error_class"),
                error_message=payload.get("message"),
            )
    result_fields["http_status"] = status_code
    result_fields["path"] = request.url.path
    result_fields["method"] = request.method
    if is_invalid_or_denied_error(payload):
        resolved = _resolved_logger(logger)
        log_info = getattr(resolved, "info", None)
        if callable(log_info):
            log_info("http exception response", **result_fields)
        else:
            _emit_warning(logger, "http exception response", **result_fields)
        return
    _emit_warning(logger, "http exception response", **result_fields)


def _maybe_extract_response_payload(response: JSONResponse) -> dict[str, Any] | None:
    body = getattr(response, "body", None)
    if not body:
        return None
    try:
        payload = json.loads(body)
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _maybe_log_error_response(
    logger: Any | None,
    request: Request,
    response: JSONResponse,
    *,
    exc: Exception | None = None,
) -> None:
    if response.status_code < 400:
        return
    payload = _maybe_extract_response_payload(response)
    if not isinstance(payload, dict):
        return
    detail = payload.get("detail")
    if isinstance(detail, dict):
        _log_exception_response(
            logger,
            request,
            status_code=response.status_code,
            payload=detail,
            exc=exc,
        )
        return
    if isinstance(detail, list):
        _log_exception_response(
            logger,
            request,
            status_code=response.status_code,
            payload={
                "message": "validation failed",
                "error_code": "SKILLHUB_VALIDATION_FAILED",
                "error_class": "validation",
            },
            exc=exc,
        )


def _maybe_audit_failed_mutation(
    request: Request | None,
    *,
    status_code: int,
    payload: dict,
) -> None:
    if request is None:
        return
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if detail is None:
        detail = payload
    audit_failed_mutation(request, status_code, detail)



def response_with_error_logging(
    logger: Any | None,
    *,
    request: Request | None = None,
    status_code: int,
    payload: dict,
    exc: Exception | None = None,
) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content={"detail": as_json_response_body(payload)})
    _maybe_audit_failed_mutation(request, status_code=status_code, payload=payload)
    if request is not None:
        _maybe_log_error_response(logger, request, response, exc=exc)
    return response


def register_exception_handlers(fastapi_app: FastAPI, *, logger: Any | None = None) -> None:
    async def publish_error_handler(request: Request, exc: PublishError):
        payload = ensure_standard_error_payload(exc.detail)
        return response_with_error_logging(
            logger,
            request=request,
            status_code=exc.status_code,
            payload=payload,
            exc=exc,
        )

    async def business_error_handler(request: Request, exc: BusinessError):
        payload = ensure_standard_error_payload(exc.detail)
        return response_with_error_logging(
            logger,
            request=request,
            status_code=exc.status_code,
            payload=payload,
            exc=exc,
        )

    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        payload = ensure_standard_error_payload(
            normalize_http_exception_detail(exc.status_code, exc.detail)
        )
        return response_with_error_logging(
            logger,
            request=request,
            status_code=exc.status_code,
            payload=payload,
            exc=exc,
        )

    async def validation_error_handler(request: Request, exc: RequestValidationError):
        payload = ensure_standard_error_payload(
            validation_error_payload(message="请求参数校验失败", details=exc.errors())
        )
        return response_with_error_logging(logger, request=request, status_code=422, payload=payload, exc=exc)

    async def unhandled_exception_handler(request: Request, exc: Exception):
        if not getattr(exc, "_unhandled_exception_logged", False):
            _emit_exception(logger, "Unhandled exception on %s: %s", request.url.path, exc)
            setattr(exc, "_unhandled_exception_logged", True)
        payload = ensure_standard_error_payload(internal_error_payload("服务器内部错误，请稍后重试"))
        return response_with_error_logging(logger, request=request, status_code=500, payload=payload, exc=exc)

    fastapi_app.add_exception_handler(PublishError, publish_error_handler)
    fastapi_app.add_exception_handler(BusinessError, business_error_handler)
    fastapi_app.add_exception_handler(StarletteHTTPException, http_error_handler)
    fastapi_app.add_exception_handler(RequestValidationError, validation_error_handler)
    fastapi_app.add_exception_handler(Exception, unhandled_exception_handler)
    fastapi_app.add_exception_handler(500, unhandled_exception_handler)
