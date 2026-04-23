from __future__ import annotations

import hashlib
from typing import Optional, TYPE_CHECKING

from shared.rich_compat import Console, Panel

from .schema import parse_json_from_response

try:
    from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError
except ModuleNotFoundError:  # pragma: no cover
    APIConnectionError = APIError = APITimeoutError = AuthenticationError = None

if TYPE_CHECKING:
    from .builder import TreeBuilder


console = Console()


class TreeLLMRuntime:
    """Owns model limits, retries, cache observability, and JSON parsing retries."""

    def __init__(self, builder: "TreeBuilder") -> None:
        self._builder = builder

    def auto_batch_size(self) -> int:
        builder = self._builder
        if builder.batch_size_cache is not None:
            return builder.batch_size_cache
        ctx_window, _ = self.model_limits()
        available = ctx_window - builder.PROMPT_OVERHEAD_TOKENS - builder.OUTPUT_RESERVE_TOKENS
        batch_size = available // builder.AVG_TOKENS_PER_SKILL
        builder.batch_size_cache = max(50, min(batch_size, 1000))
        return builder.batch_size_cache

    def get_max_output_tokens(self) -> int:
        builder = self._builder
        if builder.max_output_tokens_cache is not None:
            return builder.max_output_tokens_cache
        _, max_out = self.model_limits()
        max_output_override = int(getattr(builder.manager_config.build, "max_output_tokens", 0) or 0)
        if max_output_override > 0:
            builder.max_output_tokens_cache = max_output_override
        else:
            builder.max_output_tokens_cache = min(int(max_out), 4096)
        return builder.max_output_tokens_cache

    def merged_extra_body(self) -> dict:
        merged = {
            "thinking": {"type": "disabled"},
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0.0,
            "top_p": 1.0,
        }
        if self._builder.llm_seed is not None:
            try:
                merged["seed"] = int(self._builder.llm_seed)
            except Exception:
                merged.pop("seed", None)
        return merged

    def model_limits(self) -> tuple[int, int]:
        builder = self._builder
        ctx_cfg = int(getattr(builder.manager_config.build, "context_window", 0) or 0)
        out_cfg = int(getattr(builder.manager_config.build, "max_output_tokens", 0) or 0)
        if ctx_cfg > 0 and out_cfg > 0:
            return ctx_cfg, out_cfg

        model_name = (builder.model or "").lower()
        known_128k_models = ("gpt-4.1", "gpt-4o", "claude", "doubao")
        if any(marker in model_name for marker in known_128k_models):
            return 128000, 32768
        if "gpt-5" in model_name:
            return 200000, 65536
        return builder.DEFAULT_CONTEXT_WINDOW, builder.DEFAULT_MAX_OUTPUT_TOKENS

    @staticmethod
    def normalize_prompt_for_fingerprint(prompt: str) -> str:
        normalized_lines = []
        for line in prompt.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            normalized_lines.append(line.rstrip())
        return "\n".join(normalized_lines).strip()

    def prompt_fingerprint(self, prompt: str) -> str:
        builder = self._builder
        pieces = [
            builder.prompt_fingerprint_version,
            builder.model or "",
            self.normalize_prompt_for_fingerprint(prompt),
        ]
        digest_input = "\n".join(str(piece) for piece in pieces)
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]

    def extract_cache_hit(self, response) -> Optional[bool]:
        for mapping in self._response_metadata_candidates(response):
            parsed = self.extract_cache_hit_from_mapping(mapping)
            if parsed is not None:
                return parsed
        return None

    def extract_cache_hit_from_mapping(self, mapping: dict) -> Optional[bool]:
        aliases = {"cache_hit", "cachehit", "is_cached", "cached", "x-litellm-cache-hit", "litellm_cache_hit"}
        pending = [mapping]
        while pending:
            candidate = pending.pop(0)
            if not isinstance(candidate, dict):
                continue
            for raw_key, raw_value in candidate.items():
                key = str(raw_key).strip().lower()
                if key in aliases:
                    coerced = self._coerce_cache_flag(raw_value)
                    if coerced is not None:
                        return coerced
                if isinstance(raw_value, dict):
                    pending.append(raw_value)
        return None

    @staticmethod
    def _response_metadata_candidates(response) -> list[dict]:
        candidates: list[dict] = []
        for attr_name in ("_hidden_params", "_response_headers"):
            value = getattr(response, attr_name, None)
            if isinstance(value, dict):
                candidates.append(value)
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                candidates.append(dumped)
        return candidates

    @staticmethod
    def _coerce_cache_flag(value) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "hit", "yes"}:
                return True
            if normalized in {"0", "false", "miss", "no"}:
                return False
        return None

    def record_cache_observation(self, cache_hit: Optional[bool]) -> None:
        builder = self._builder
        bucket_name = "unknown"
        if cache_hit is True:
            bucket_name = "hits"
        elif cache_hit is False:
            bucket_name = "misses"
        attr_name = f"cache_{bucket_name}"
        setattr(builder, attr_name, getattr(builder, attr_name) + 1)

    def print_cache_stats(self) -> None:
        builder = self._builder
        if not builder.cache_observability:
            return
        known_total = builder.cache_hits + builder.cache_misses
        observed_hit_rate = (builder.cache_hits / known_total * 100.0) if known_total else 0.0
        lower_bound_hit_rate = (builder.cache_hits / builder.llm_calls * 100.0) if builder.llm_calls else 0.0
        metrics = {
            "LLM calls": builder.llm_calls,
            "Retry calls": builder.retry_calls,
            "Cache hits/misses/unknown": f"{builder.cache_hits}/{builder.cache_misses}/{builder.cache_unknown}",
            "Observed hit rate (known only)": f"{observed_hit_rate:.1f}%",
            "Estimated hit rate lower bound": f"{lower_bound_hit_rate:.1f}%",
            "Unique prompt fingerprints": len(builder.prompt_fingerprints),
        }
        lines = [f"{label}: {value}" for label, value in metrics.items()]
        console.print(Panel("\n".join(lines), title="[bold cyan]Cache Stats[/bold cyan]", border_style="cyan"))

    def call_llm(self, prompt: str, is_retry: bool = False, retry_left: int | None = None) -> str:
        builder = self._builder
        if builder.client is None:
            raise RuntimeError("openai is required to build the tree. Please install the openai package first.")
        mcfg = builder.manager_config
        if retry_left is None:
            retry_left = int(mcfg.build.num_retries)
        max_tokens = self.get_max_output_tokens()
        prompt_fingerprint = self.prompt_fingerprint(prompt)
        with builder.counter_lock:
            builder.llm_calls += 1
            if is_retry:
                builder.retry_calls += 1
            if builder.cache_observability:
                builder.prompt_fingerprints.add(prompt_fingerprint)
            if builder.progress and builder.progress_task is not None:
                builder.progress.update(builder.progress_task, llm=builder.llm_calls)
        try:
            with builder.llm_semaphore:
                response = builder.client.chat.completions.create(
                    model=builder.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    timeout=mcfg.build.timeout,
                    extra_body=self.merged_extra_body(),
                )
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                builder.thread_local.truncated = True
                console.print(Panel(
                    "[bold red]OUTPUT TRUNCATED![/bold red]\n"
                    f"The LLM response was cut off at {max_tokens} tokens (finish_reason='length').\n"
                    "This will cause incomplete JSON parsing and skill loss.\n"
                    "Consider reducing batch size or increasing max_tokens.",
                    title="[bold red]Truncation Warning[/bold red]",
                    border_style="red",
                ))
            else:
                builder.thread_local.truncated = False
            with builder.counter_lock:
                builder.consecutive_failures = 0
                if builder.cache_observability:
                    self.record_cache_observation(None)
            return response.choices[0].message.content or "{}"
        except Exception as e:
            if AuthenticationError is not None and isinstance(e, AuthenticationError):
                console.print("[red]Authentication failed - check API key[/red]")
                raise
            err_text = str(e).lower()
            is_context_exceeded = any(
                marker in err_text
                for marker in ("context length", "maximum context", "too many tokens", "max context")
            )
            if is_context_exceeded:
                console.print(f"[red]Context window exceeded: {e}[/red]")
                if builder.batch_size_cache and builder.batch_size_cache > 50:
                    builder.batch_size_cache = max(50, builder.batch_size_cache // 2)
                    console.print(f"[yellow]Reduced batch size to {builder.batch_size_cache}[/yellow]")
                with builder.counter_lock:
                    builder.consecutive_failures += 1
                    if builder.consecutive_failures >= builder.MAX_CONSECUTIVE_FAILURES:
                        raise RuntimeError(
                            f"Circuit breaker: {builder.consecutive_failures} consecutive LLM failures"
                        ) from e
                return "{}"
            is_transient = (
                (APITimeoutError is not None and isinstance(e, APITimeoutError))
                or (APIConnectionError is not None and isinstance(e, APIConnectionError))
                or (APIError is not None and isinstance(e, APIError))
                or "timed out" in err_text
                or "timeout" in err_text
            )
            if is_transient and retry_left > 0:
                return self.call_llm(prompt, is_retry=True, retry_left=retry_left - 1)
            console.print(f"[red]LLM call failed: {e}[/red]")
            with builder.counter_lock:
                builder.consecutive_failures += 1
                if builder.consecutive_failures >= builder.MAX_CONSECUTIVE_FAILURES:
                    raise RuntimeError(
                        f"Circuit breaker: {builder.consecutive_failures} consecutive LLM failures"
                    ) from e
            return "{}"

    def call_llm_json(self, prompt: str, max_retries: int = 3, is_retry: bool = False) -> dict:
        builder = self._builder
        attempts_remaining = max_retries
        attempt_index = 0
        while attempts_remaining > 0:
            builder.thread_local.truncated = False
            response = self.call_llm(prompt, is_retry=is_retry or attempt_index > 0)
            parsed = parse_json_from_response(response, default={})
            if isinstance(parsed, dict):
                return parsed
            if getattr(builder.thread_local, "truncated", False):
                console.print("[yellow]Skipping retry because the model output was truncated[/yellow]")
                return {}
            console.print(
                f"[yellow]Expected a JSON object but received {type(parsed).__name__} "
                f"(attempt {attempt_index + 1}/{max_retries})[/yellow]"
            )
            attempt_index += 1
            attempts_remaining -= 1
        console.print("[red]All retries exhausted, returning empty dict[/red]")
        return {}
