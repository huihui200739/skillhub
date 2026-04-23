"""Wrapper for OpenAI LLM client."""

from typing import Any, Optional


class LLMClient:
    """Wrapper for OpenAI-compatible LLM client that provides a complete() method."""

    def __init__(self, openai_client: Any) -> None:
        """Initialize with an OpenAI client instance."""
        self._client = openai_client

    def complete(self, model: str, messages: list, **kwargs) -> Any:
        """Call the LLM completion API."""
        return self._client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs
        )

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the wrapped client."""
        return getattr(self._client, name)
