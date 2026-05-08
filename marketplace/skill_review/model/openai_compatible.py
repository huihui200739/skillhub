from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from skill_review.model.errors import SkillReviewSemanticRuntimeError
from skill_review.model.types import SemanticReviewRequest, SemanticReviewResult


@dataclass(frozen=True)
class OpenAICompatibleSemanticConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    display_name: str
    temperature: float = 0.0


class OpenAICompatibleSemanticReviewClient:
    def __init__(self, config: OpenAICompatibleSemanticConfig) -> None:
        self.config = config
        self.model_name = config.display_name

    def review(self, request: SemanticReviewRequest) -> SemanticReviewResult:
        request_body = build_chat_completion_request(request, self.config)
        response_body = request_chat_completion(request_body, self.config)
        response_content = extract_message_content(response_body, request_body)
        parsed_response = parse_semantic_response(response_content, request_body, response_body)
        return SemanticReviewResult(
            engine="openai-compatible-chat-completions",
            model_name=self.config.display_name,
            response=parsed_response,
            token_usage=extract_token_usage(response_body.get("usage") if isinstance(response_body, dict) else None),
            raw_response=response_body,
        )


def build_chat_completion_request(
    request: SemanticReviewRequest,
    config: OpenAICompatibleSemanticConfig,
) -> dict[str, Any]:
    return {
        "model": config.model,
        "temperature": config.temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": json.dumps(request.prompt_payload, ensure_ascii=False)},
        ],
    }


def request_chat_completion(
    request_body: dict[str, Any],
    config: OpenAICompatibleSemanticConfig,
) -> dict[str, Any]:
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=config.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=request_body)
    except httpx.TimeoutException as exc:
        raise SkillReviewSemanticRuntimeError(
            f"skill review semantic request timed out after {config.timeout_seconds}s",
            request_body=request_body,
        ) from exc
    except httpx.HTTPError as exc:
        raise SkillReviewSemanticRuntimeError(
            f"skill review semantic request failed: {exc}",
            request_body=request_body,
        ) from exc

    if response.status_code >= 400:
        raise SkillReviewSemanticRuntimeError(
            f"skill review semantic request failed with status {response.status_code}: {response.text}",
            request_body=request_body,
            response_content=response.text,
        )
    try:
        parsed = response.json()
    except ValueError as exc:
        raise SkillReviewSemanticRuntimeError(
            "skill review semantic endpoint returned non-JSON response",
            request_body=request_body,
            response_content=response.text,
        ) from exc
    return parsed


def extract_message_content(response_body: dict[str, Any], request_body: dict[str, Any]) -> str:
    try:
        content = response_body["choices"][0]["message"]["content"]
    except Exception as exc:
        raise SkillReviewSemanticRuntimeError(
            "skill review semantic response missing message content",
            request_body=request_body,
            response_body=response_body,
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise SkillReviewSemanticRuntimeError(
            "skill review semantic response missing message content",
            request_body=request_body,
            response_body=response_body,
        )
    return content


def parse_semantic_response(
    content: str,
    request_body: dict[str, Any],
    response_body: dict[str, Any],
) -> dict[str, Any]:
    trimmed = strip_json_markdown(content)
    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise SkillReviewSemanticRuntimeError(
            "skill review semantic response is not valid JSON",
            request_body=request_body,
            response_body=response_body,
            response_content=content,
        ) from exc
    if not is_valid_semantic_response(parsed):
        raise SkillReviewSemanticRuntimeError(
            "skill review semantic response is not a valid semantic review object",
            request_body=request_body,
            response_body=response_body,
            response_content=content,
        )
    return parsed


def strip_json_markdown(content: str) -> str:
    trimmed = str(content).strip()
    trimmed = re.sub(r"^```json\s*", "", trimmed, flags=re.I)
    trimmed = re.sub(r"^```\s*", "", trimmed)
    return re.sub(r"\s*```$", "", trimmed).strip()


def is_valid_semantic_response(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("conclusion"), str)
        and isinstance(value.get("findings"), list)
        and isinstance(value.get("candidate_reviews"), list)
    )


def extract_token_usage(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    token_usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            token_usage[key] = int(value)
    return token_usage or None
