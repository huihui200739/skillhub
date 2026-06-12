# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from plugins_market.core.context import get_duration_ms, get_request_id

_operation_id_var: ContextVar[str | None] = ContextVar("operation_id", default=None)
_operation_type_var: ContextVar[str | None] = ContextVar("operation_type", default=None)
_parent_operation_id_var: ContextVar[str | None] = ContextVar("parent_operation_id", default=None)
_actor_id_var: ContextVar[str | None] = ContextVar("operation_actor_id", default=None)
_actor_name_var: ContextVar[str | None] = ContextVar("operation_actor_name", default=None)
_actor_type_var: ContextVar[str | None] = ContextVar("operation_actor_type", default=None)
_resource_type_var: ContextVar[str | None] = ContextVar("operation_resource_type", default=None)
_resource_id_var: ContextVar[str | None] = ContextVar("operation_resource_id", default=None)
_resource_version_var: ContextVar[str | None] = ContextVar("operation_resource_version", default=None)
_started_at_var: ContextVar[str | None] = ContextVar("operation_started_at", default=None)
_attempt_no_var: ContextVar[int | None] = ContextVar("operation_attempt_no", default=None)
_retry_of_var: ContextVar[str | None] = ContextVar("operation_retry_of", default=None)
_operation_completion_logged_var: ContextVar[bool] = ContextVar("operation_completion_logged", default=False)


@dataclass(frozen=True, slots=True)
class OperationContext:
    operation_id: str
    operation_type: str
    parent_operation_id: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_type: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    resource_version: str | None = None
    started_at: str | None = None
    attempt_no: int | None = None
    retry_of: str | None = None


@dataclass(frozen=True, slots=True)
class OperationActor:
    actor_id: str | None = None
    actor_name: str | None = None
    actor_type: str | None = None


@dataclass(frozen=True, slots=True)
class OperationResource:
    resource_type: str | None = None
    resource_id: str | None = None
    resource_version: str | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    stage: str
    result: str
    error_code: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    result_detail: str | None = None


@dataclass(frozen=True, slots=True)
class OperationContextSnapshot:
    context: OperationContext | None
    completion_logged: bool = False


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _clean_str(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _clean_attempt_no(value: int | None) -> int | None:
    if value is None:
        return None
    return max(1, int(value))


def generate_operation_id() -> str:
    return f"op_{uuid.uuid4().hex}"


def get_operation_id() -> str | None:
    return _operation_id_var.get()


def get_operation_type() -> str | None:
    return _operation_type_var.get()


def get_parent_operation_id() -> str | None:
    return _parent_operation_id_var.get()


def get_operation_actor_id() -> str | None:
    return _actor_id_var.get()


def get_operation_actor_name() -> str | None:
    return _actor_name_var.get()


def get_operation_actor_type() -> str | None:
    return _actor_type_var.get()


def get_operation_resource_type() -> str | None:
    return _resource_type_var.get()


def get_operation_resource_id() -> str | None:
    return _resource_id_var.get()


def get_operation_resource_version() -> str | None:
    return _resource_version_var.get()


def get_operation_started_at() -> str | None:
    return _started_at_var.get()


def get_operation_attempt_no() -> int | None:
    return _attempt_no_var.get()


def get_operation_retry_of() -> str | None:
    return _retry_of_var.get()


def has_operation_completion_log() -> bool:
    return _operation_completion_logged_var.get()


def mark_operation_completion_logged() -> None:
    _operation_completion_logged_var.set(True)


def _build_operation_context() -> OperationContext | None:
    operation_id = get_operation_id()
    operation_type = get_operation_type()
    if not operation_id or not operation_type:
        return None
    return OperationContext(
        operation_id=operation_id,
        operation_type=operation_type,
        parent_operation_id=get_parent_operation_id(),
        actor_id=get_operation_actor_id(),
        actor_name=get_operation_actor_name(),
        actor_type=get_operation_actor_type(),
        resource_type=get_operation_resource_type(),
        resource_id=get_operation_resource_id(),
        resource_version=get_operation_resource_version(),
        started_at=get_operation_started_at(),
        attempt_no=get_operation_attempt_no(),
        retry_of=get_operation_retry_of(),
    )


def get_operation_context() -> OperationContext | None:
    return _build_operation_context()


def has_operation_context() -> bool:
    return _build_operation_context() is not None


def _set_optional_str(var: ContextVar[str | None], value: str | None) -> None:
    var.set(_clean_str(value))


def _set_operation_actor(
    *,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str | None = None,
) -> None:
    _set_optional_str(_actor_id_var, actor_id)
    _set_optional_str(_actor_name_var, actor_name)
    _set_optional_str(_actor_type_var, actor_type)


def _set_operation_resource(
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_version: str | None = None,
) -> None:
    _set_optional_str(_resource_type_var, resource_type)
    _set_optional_str(_resource_id_var, resource_id)
    _set_optional_str(_resource_version_var, resource_version)


def _set_operation_timing(
    *,
    started_at: str | None = None,
    attempt_no: int | None = None,
    retry_of: str | None = None,
) -> None:
    _set_optional_str(_started_at_var, started_at or _now_utc_iso())
    _attempt_no_var.set(_clean_attempt_no(attempt_no) or 1)
    _set_optional_str(_retry_of_var, retry_of)


def set_operation_context(
    *,
    operation_id: str | None = None,
    operation_type: str,
    parent_operation_id: str | None = None,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_version: str | None = None,
    attempt_no: int | None = None,
    retry_of: str | None = None,
    started_at: str | None = None,
) -> OperationContext:
    resolved_operation_id = _clean_str(operation_id) or generate_operation_id()
    _operation_id_var.set(resolved_operation_id)
    _operation_type_var.set(operation_type)
    _parent_operation_id_var.set(_clean_str(parent_operation_id))
    _set_operation_actor(
        actor_id=actor_id,
        actor_name=actor_name,
        actor_type=actor_type,
    )
    _set_operation_resource(
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version=resource_version,
    )
    _set_operation_timing(
        started_at=started_at,
        attempt_no=attempt_no,
        retry_of=retry_of,
    )
    return _build_operation_context() or OperationContext(
        operation_id=resolved_operation_id,
        operation_type=operation_type,
    )


def start_operation(
    *,
    operation_type: str,
    operation_id: str | None = None,
    parent_operation_id: str | None = None,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_version: str | None = None,
    attempt_no: int | None = None,
    retry_of: str | None = None,
) -> OperationContext:
    return set_operation_context(
        operation_id=operation_id,
        operation_type=operation_type,
        parent_operation_id=parent_operation_id,
        actor_id=actor_id,
        actor_name=actor_name,
        actor_type=actor_type,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version=resource_version,
        attempt_no=attempt_no,
        retry_of=retry_of,
    )


def bind_operation_actor(
    *,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str | None = None,
) -> OperationActor:
    _set_operation_actor(actor_id=actor_id, actor_name=actor_name, actor_type=actor_type)
    return OperationActor(
        actor_id=get_operation_actor_id(),
        actor_name=get_operation_actor_name(),
        actor_type=get_operation_actor_type(),
    )


def bind_operation_resource(
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_version: str | None = None,
) -> OperationResource:
    _set_operation_resource(resource_type=resource_type, resource_id=resource_id, resource_version=resource_version)
    return OperationResource(
        resource_type=get_operation_resource_type(),
        resource_id=get_operation_resource_id(),
        resource_version=get_operation_resource_version(),
    )


def clear_operation_context() -> None:
    _operation_id_var.set(None)
    _operation_type_var.set(None)
    _parent_operation_id_var.set(None)
    _actor_id_var.set(None)
    _actor_name_var.set(None)
    _actor_type_var.set(None)
    _resource_type_var.set(None)
    _resource_id_var.set(None)
    _resource_version_var.set(None)
    _started_at_var.set(None)
    _attempt_no_var.set(None)
    _retry_of_var.set(None)
    _operation_completion_logged_var.set(False)


def capture_operation_snapshot() -> OperationContextSnapshot:
    return OperationContextSnapshot(
        context=_build_operation_context(),
        completion_logged=has_operation_completion_log(),
    )


def attach_operation_snapshot(exc: Exception) -> None:
    if getattr(exc, "_operation_snapshot", None) is not None:
        return
    setattr(exc, "_operation_snapshot", capture_operation_snapshot())


@contextmanager
def operation_context(
    *,
    operation_type: str,
    operation_id: str | None = None,
    parent_operation_id: str | None = None,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_version: str | None = None,
    attempt_no: int | None = None,
    retry_of: str | None = None,
) -> Iterator[OperationContext]:
    previous = _build_operation_context()
    previous_completion_logged = has_operation_completion_log()
    inherited_parent_operation_id = _clean_str(parent_operation_id) or (previous.operation_id if previous else None)
    current = set_operation_context(
        operation_id=operation_id,
        operation_type=operation_type,
        parent_operation_id=inherited_parent_operation_id,
        actor_id=actor_id if actor_id is not None else (previous.actor_id if previous else None),
        actor_name=actor_name if actor_name is not None else (previous.actor_name if previous else None),
        actor_type=actor_type if actor_type is not None else (previous.actor_type if previous else None),
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version=resource_version,
        attempt_no=attempt_no if attempt_no is not None else (previous.attempt_no if previous else None),
        retry_of=retry_of if retry_of is not None else (previous.retry_of if previous else None),
    )
    try:
        yield current
    except Exception as exc:
        attach_operation_snapshot(exc)
        raise
    finally:
        if previous is None:
            clear_operation_context()
        else:
            set_operation_context(
                operation_id=previous.operation_id,
                operation_type=previous.operation_type,
                parent_operation_id=previous.parent_operation_id,
                actor_id=previous.actor_id,
                actor_name=previous.actor_name,
                actor_type=previous.actor_type,
                resource_type=previous.resource_type,
                resource_id=previous.resource_id,
                resource_version=previous.resource_version,
                attempt_no=previous.attempt_no,
                retry_of=previous.retry_of,
                started_at=previous.started_at,
            )
            _operation_completion_logged_var.set(previous_completion_logged)


def get_current_operation_result_defaults() -> dict[str, Any]:
    context = _build_operation_context()
    if context is None:
        return {}
    return {
        "operation_id": context.operation_id,
        "operation_type": context.operation_type,
        "parent_operation_id": context.parent_operation_id,
        "actor_id": context.actor_id,
        "actor_name": context.actor_name,
        "actor_type": context.actor_type,
        "resource_type": context.resource_type,
        "resource_id": context.resource_id,
        "resource_version": context.resource_version,
        "started_at": context.started_at,
        "attempt_no": context.attempt_no,
        "retry_of": context.retry_of,
    }


def operation_log_fields_from_context(
    context: OperationContext | None,
    *,
    stage: str,
    result: str,
    error_code: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    result_detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "stage": stage,
        "result": result,
    }
    request_id = get_request_id()
    if request_id:
        fields["request_id"] = request_id
    if context is not None:
        fields["operation_id"] = context.operation_id
        fields["operation_type"] = context.operation_type
        if context.parent_operation_id:
            fields["parent_operation_id"] = context.parent_operation_id
        if context.actor_id:
            fields["actor_id"] = context.actor_id
        if context.actor_name:
            fields["actor_name"] = context.actor_name
        if context.actor_type:
            fields["actor_type"] = context.actor_type
        if context.resource_type:
            fields["resource_type"] = context.resource_type
        if context.resource_id:
            fields["resource_id"] = context.resource_id
        if context.resource_version:
            fields["resource_version"] = context.resource_version
        if context.started_at:
            fields["started_at"] = context.started_at
        if context.attempt_no is not None:
            fields["attempt_no"] = context.attempt_no
        if context.retry_of:
            fields["retry_of"] = context.retry_of
    duration_ms = get_duration_ms()
    if duration_ms > 0:
        fields["duration_ms"] = duration_ms
    if error_code:
        fields["error_code"] = error_code
    if error_class:
        fields["error_class"] = error_class
    if error_message:
        fields["error_message"] = error_message
    if result_detail:
        fields["result_detail"] = result_detail
    for key, value in extra.items():
        if value is not None:
            fields[key] = value
    return fields


def operation_log_fields(
    *,
    stage: str,
    result: str,
    error_code: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    result_detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return operation_log_fields_from_context(
        _build_operation_context(),
        stage=stage,
        result=result,
        error_code=error_code,
        error_class=error_class,
        error_message=error_message,
        result_detail=result_detail,
        **extra,
    )


def log_operation_event(
    *,
    stage: str,
    result: str,
    error_code: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    result_detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if stage == "complete":
        mark_operation_completion_logged()
    return operation_log_fields(
        stage=stage,
        result=result,
        error_code=error_code,
        error_class=error_class,
        error_message=error_message,
        result_detail=result_detail,
        **extra,
    )


def is_invalid_or_denied_error(payload: dict[str, Any]) -> bool:
    return operation_failure_result(payload).result in {"invalid", "denied"}


def sanitize_error_message(value: str | None, *, fallback: str | None = None) -> str | None:
    cleaned = _clean_str(value)
    if not cleaned:
        return fallback
    return cleaned.replace("\r", " ").replace("\n", " ")


def safe_error_summary(
    *,
    error_code: str | None = None,
    error_class: str | None = None,
    fallback: str = "operation failed",
) -> str:
    if error_code and error_class:
        return f"{error_class}:{error_code}"
    if error_code:
        return error_code
    if error_class:
        return error_class
    return fallback


def failure_log_fields_from_payload(payload: dict[str, Any], *, default_result: str = "failure") -> dict[str, Any]:
    operation_result = operation_failure_result(payload)
    if default_result != "failure" and operation_result.result == "failure":
        operation_result = OperationResult(
            stage=operation_result.stage,
            result=default_result,
            error_code=operation_result.error_code,
            error_class=operation_result.error_class,
            error_message=operation_result.error_message,
            result_detail=operation_result.result_detail,
        )
    return apply_operation_result(operation_result)


def operation_failure_result(payload: dict[str, Any]) -> OperationResult:
    error_class = payload.get("error_class")
    result = "failure"
    if error_class == "permission":
        result = "denied"
    elif error_class in {"validation", "auth", "not_found", "conflict"}:
        result = "invalid"
    return OperationResult(
        stage="complete",
        result=result,
        error_code=payload.get("error_code"),
        error_class=error_class,
        error_message=payload.get("message"),
    )


def apply_operation_result(result: OperationResult, **extra: Any) -> dict[str, Any]:
    return log_operation_event(
        stage=result.stage,
        result=result.result,
        error_code=result.error_code,
        error_class=result.error_class,
        error_message=result.error_message,
        result_detail=result.result_detail,
        **extra,
    )


def complete_operation_result(
    *,
    stage: str = "complete",
    result: str,
    error_code: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    result_detail: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return log_operation_event(
        stage=stage,
        result=result,
        error_code=error_code,
        error_class=error_class,
        error_message=error_message,
        result_detail=result_detail,
        **extra,
    )

