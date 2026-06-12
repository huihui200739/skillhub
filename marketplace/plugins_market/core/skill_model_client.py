from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from common.security.security_utils import SecurityUtils
from plugins_market.core.config import settings
from skill_review.model.openai_compatible import (
    OpenAICompatibleSemanticConfig,
    OpenAICompatibleSemanticReviewClient,
)


@dataclass
class SkillModelConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    display_name: str


def _infer_endpoint_id(base_url: str) -> str:
    hostname = (urlparse(base_url).hostname or "").lower()
    if "siliconflow" in hostname:
        return "siliconflow"
    if "dashscope" in hostname or "aliyuncs" in hostname:
        return "aliyun"
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return "local"
    if hostname.startswith("api."):
        hostname = hostname[4:]
    return hostname.split(".", 1)[0] or "custom"


def resolve_skill_review_model_config() -> SkillModelConfig | None:
    base_url = (settings.skill_review_model_base_url or "").strip()
    model = (settings.skill_review_model_name or "").strip()
    timeout_seconds = int(settings.skill_review_model_timeout_seconds)

    review_api_key = SecurityUtils.get_decrypt_secret("MARKET_SKILL_REVIEW_MODEL_API_KEY", default="") or ""
    api_key = review_api_key.strip()

    if not base_url or not model or not api_key:
        return None

    return SkillModelConfig(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        timeout_seconds=max(1, timeout_seconds),
        display_name=f"{_infer_endpoint_id(base_url)}/{model}",
    )


def create_skill_review_semantic_client() -> OpenAICompatibleSemanticReviewClient | None:
    config = resolve_skill_review_model_config()
    if config is None:
        return None
    return OpenAICompatibleSemanticReviewClient(
        OpenAICompatibleSemanticConfig(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            display_name=config.display_name,
        )
    )
