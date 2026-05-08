from __future__ import annotations

from typing import Any


class SkillReviewSemanticRuntimeError(Exception):
    def __init__(
        self,
        message: str,
        *,
        request_body: dict[str, Any] | None = None,
        response_body: Any | None = None,
        response_content: str | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.request_body = request_body
        self.response_body = response_body
        self.response_content = response_content
        self.token_usage = token_usage
        self.trace_id: str | None = None
        self.model_name: str | None = None
        self.policy_version: str | None = None
        self.engine: str | None = None
