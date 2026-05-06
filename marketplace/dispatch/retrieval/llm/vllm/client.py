from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import threading
import uuid
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Mapping, Sequence

from retrieval.llm.base import (
    GenerationConfig,
    LLMClientCapabilities,
    LLMRequestError,
    LLMStreamChunk,
    MaxNewTokensTooLarge,
    Message,
    PrefixCacheUnavailable,
    ProgressiveLLMClient,
    QueryTooLongForPrefixCache,
    UnsupportedCapability,
)
from retrieval.llm.base.tokenization import join_messages

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalVLLMPrefixCacheHandle:
    cache_id: str
    prefix_token_ids: tuple[int, ...]
    prefix_len: int
    prefix_token_hash: str
    model_fingerprint: str
    tokenizer_fingerprint: str
    prefix_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def dp_replica_id(self) -> int | None:
        return None


class _AsyncLoopRunner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="local-vllm-async-loop", daemon=True)
        self._thread.start()

    def submit(self, coro: Any, *, timeout: float | None = None) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()


@dataclass
class LocalVLLMClient(ProgressiveLLMClient):
    engine: object
    tokenizer: object
    model_name: str
    model_path: str = ""
    tokenizer_path: str = ""
    enable_prefix_caching: bool = True
    warmup_max_tokens: int = 1
    max_suffix_tokens: int = 256
    max_new_tokens: int = 128
    request_timeout: float | None = None
    sampling_params_cls: object | None = None
    tokens_prompt_cls: object | None = None
    stop_token_ids: tuple[int, ...] = ()
    _handles: dict[str, LocalVLLMPrefixCacheHandle] = field(default_factory=dict)
    _loop_runner: _AsyncLoopRunner = field(default_factory=_AsyncLoopRunner)

    name = "local_vllm"

    @classmethod
    def from_pretrained(
        cls,
        *,
        model_path: str,
        tokenizer_path: str | None = None,
        device: str = "auto",
        dtype: str = "auto",
        enable_prefix_caching: bool = True,
        vllm_kwargs: Mapping[str, Any] | None = None,
        generation_client: ProgressiveLLMClient | None = None,
        max_suffix_tokens: int = 256,
        max_new_tokens: int = 128,
    ) -> "LocalVLLMClient":
        del generation_client
        try:
            from transformers import AutoTokenizer
            from vllm.engine.arg_utils import AsyncEngineArgs
            from vllm.engine.async_llm_engine import AsyncLLMEngine
            from vllm.inputs import TokensPrompt
            from vllm.sampling_params import SamplingParams
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("vllm and transformers are required for LocalVLLMClient") from exc

        resolved_tokenizer_path = tokenizer_path or model_path
        options = dict(vllm_kwargs or {})
        request_model_name = (
            str(options.pop("request_model", "") or options.pop("model_name", "") or model_path).strip() or model_path
        )
        warmup_max_tokens = max(1, int(options.pop("prefix_cache_warmup_max_tokens", 1)))
        qwen_im_end_token_id = options.pop("qwen_im_end_token_id", 151643)
        trust_remote_code = bool(options.pop("trust_remote_code", True))

        tokenizer = AutoTokenizer.from_pretrained(
            resolved_tokenizer_path,
            trust_remote_code=trust_remote_code,
        )
        stop_token_ids = _default_stop_token_ids(tokenizer, qwen_im_end_token_id=qwen_im_end_token_id)

        engine_kwargs: dict[str, Any] = {
            "model": model_path,
            "tokenizer": resolved_tokenizer_path,
            "trust_remote_code": trust_remote_code,
        }
        if str(dtype or "").strip():
            engine_kwargs["dtype"] = dtype
        if str(device or "").strip().lower() not in {"", "auto"} and _supports_callable_kwarg(
            AsyncEngineArgs, "device"
        ):
            engine_kwargs["device"] = str(device).strip()
        if "enable_prefix_caching" not in options:
            engine_kwargs["enable_prefix_caching"] = bool(enable_prefix_caching)
        engine_kwargs.update(options)

        LOGGER.info(
            "initializing local vllm async client model=%s tokenizer=%s dtype=%s "
            "device=%s enable_prefix_caching=%s kwargs=%s",
            model_path,
            resolved_tokenizer_path,
            dtype,
            device,
            bool(enable_prefix_caching),
            sorted(engine_kwargs.keys()),
        )
        engine_args = build_engine_args(AsyncEngineArgs, **engine_kwargs)
        engine = AsyncLLMEngine.from_engine_args(engine_args)
        return cls(
            engine=engine,
            tokenizer=tokenizer,
            model_name=request_model_name,
            model_path=str(model_path),
            tokenizer_path=str(resolved_tokenizer_path),
            enable_prefix_caching=bool(enable_prefix_caching),
            warmup_max_tokens=warmup_max_tokens,
            max_suffix_tokens=max(1, int(max_suffix_tokens)),
            max_new_tokens=max(1, int(max_new_tokens)),
            sampling_params_cls=SamplingParams,
            tokens_prompt_cls=TokensPrompt,
            stop_token_ids=stop_token_ids,
        )

    @property
    def capabilities(self) -> LLMClientCapabilities:
        return LLMClientCapabilities(
            completion=True,
            streaming=False,
            candidate_scoring=False,
            trie_constrained_decoding=False,
            progressive_prefix_kv_cache=True,
            thread_safe=True,
            local_resources=True,
        )

    def prepare_prefix_cache(
        self,
        *,
        cache_id: str,
        prefix_messages: Sequence[Message],
        prefix_token_hash: str = "",
        metadata: dict[str, object] | None = None,
    ) -> LocalVLLMPrefixCacheHandle:
        started = perf_counter()
        resolved_cache_id = str(cache_id)
        cached = self._handles.get(resolved_cache_id)
        if cached is not None:
            LOGGER.debug(
                "local vllm prefix cache already prepared cache_id=%s prefix_len=%s", cache_id, cached.prefix_len
            )
            return cached
        token_ids, prefix_text = self._encode_prefix_messages(prefix_messages)
        handle = LocalVLLMPrefixCacheHandle(
            cache_id=resolved_cache_id,
            prefix_token_ids=token_ids,
            prefix_len=len(token_ids),
            prefix_token_hash=str(prefix_token_hash or _hash_token_ids(token_ids)),
            model_fingerprint=str(self.model_name),
            tokenizer_fingerprint=_tokenizer_fingerprint(self.tokenizer),
            prefix_text=prefix_text,
            metadata=dict(metadata or {}),
        )
        if self.enable_prefix_caching and token_ids:
            try:
                self._warmup_prefix(handle)
            except Exception:
                self._handles.pop(resolved_cache_id, None)
                raise
        self._handles[resolved_cache_id] = handle
        LOGGER.debug(
            "local vllm prefix cache prepared cache_id=%s prefix_len=%s elapsed_ms=%.3f metadata=%s",
            handle.cache_id,
            handle.prefix_len,
            (perf_counter() - started) * 1000.0,
            handle.metadata,
        )
        return handle

    def get_prompt_cache_handle(self, cache_id: str) -> LocalVLLMPrefixCacheHandle | None:
        handle = self._handles.get(str(cache_id))
        LOGGER.debug("local vllm prefix cache handle lookup cache_id=%s hit=%s", cache_id, handle is not None)
        return handle

    def complete(
        self,
        model: str,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        stop_sequences: Sequence[str] | None = None,
        generation_config: GenerationConfig | None = None,
        n: int = 1,
        request_timeout: float | None = None,
    ) -> list[str]:
        del model
        if n != 1:
            raise UnsupportedCapability("LocalVLLMClient supports n=1 only")
        config = generation_config or GenerationConfig()
        if config.constraints.trie is not None:
            raise UnsupportedCapability("LocalVLLMClient does not support trie constrained decoding")
        resolved_max_tokens = max(1, int(max_tokens or self.max_new_tokens))
        if resolved_max_tokens > self.max_new_tokens:
            raise MaxNewTokensTooLarge(
                f"requested max_tokens={resolved_max_tokens} exceeds local vllm "
                f"prefix-cache budget={self.max_new_tokens}"
            )
        prompt_ids = self._resolve_prompt_token_ids(messages=messages, generation_config=config)
        sampling_params = self._sampling_params(
            max_tokens=resolved_max_tokens,
            generation_config=config,
            stop_sequences=stop_sequences,
            detokenize=True,
        )
        started = perf_counter()
        try:
            text = self._generate_sync(
                prompt_ids=prompt_ids,
                sampling_params=sampling_params,
                request_timeout=request_timeout,
            )
        except Exception as exc:
            raise LLMRequestError(f"local vLLM generation failed: {exc}") from exc
        LOGGER.debug(
            "local vllm completion complete prompt_tokens=%s max_tokens=%s elapsed_ms=%.3f",
            len(prompt_ids),
            resolved_max_tokens,
            (perf_counter() - started) * 1000.0,
        )
        return [text]

    def stream_complete(
        self,
        model: str,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        stop_sequences: Sequence[str] | None = None,
        generation_config: GenerationConfig | None = None,
        request_timeout: float | None = None,
        early_stop: object | None = None,
    ):
        del early_stop
        started = perf_counter()
        outputs = self.complete(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stop_sequences=stop_sequences,
            generation_config=generation_config,
            n=1,
            request_timeout=request_timeout,
        )
        yield LLMStreamChunk(
            outputs[0] if outputs else "",
            usage={"latency": {"total_client_ms": round((perf_counter() - started) * 1000.0, 3)}},
        )

    def _warmup_prefix(self, handle: LocalVLLMPrefixCacheHandle) -> None:
        sampling_params = self._sampling_params(
            max_tokens=self.warmup_max_tokens,
            generation_config=GenerationConfig(),
            stop_sequences=None,
            detokenize=False,
        )
        try:
            self._generate_sync(
                prompt_ids=handle.prefix_token_ids,
                sampling_params=sampling_params,
                request_timeout=self.request_timeout,
            )
        except Exception as exc:
            raise LLMRequestError(f"local vLLM prefix-cache warmup failed: {exc}") from exc

    def _resolve_prompt_token_ids(
        self,
        *,
        messages: list[Message],
        generation_config: GenerationConfig,
    ) -> tuple[int, ...]:
        hint = generation_config.prompt_cache
        if hint is None or hint.handle is None:
            return tuple(self._encode_messages(messages))
        handle = hint.handle
        if not isinstance(handle, LocalVLLMPrefixCacheHandle):
            raise PrefixCacheUnavailable(f"unsupported local vllm prefix cache handle: {type(handle).__name__}")
        if hint.expected_prefix_len is not None and int(hint.expected_prefix_len) != int(handle.prefix_len):
            raise PrefixCacheUnavailable(
                f"prefix length mismatch: expected={hint.expected_prefix_len} actual={handle.prefix_len}"
            )
        if hint.suffix_token_ids is not None:
            suffix_ids = tuple(int(token_id) for token_id in hint.suffix_token_ids)
            suffix_source = "hint_token_ids"
        else:
            suffix_ids = tuple(
                self._encode_cached_suffix(messages=messages, handle=handle, suffix_text=hint.suffix_text)
            )
            suffix_source = "rendered_chat_suffix"
        if len(suffix_ids) > self.max_suffix_tokens:
            raise QueryTooLongForPrefixCache(
                f"suffix token length={len(suffix_ids)} exceeds local vllm prefix-cache budget={self.max_suffix_tokens}"
            )
        prompt_ids = handle.prefix_token_ids + suffix_ids
        LOGGER.debug(
            "local vllm using prefix cache cache_id=%s prefix_len=%s suffix_tokens=%s "
            "suffix_source=%s prompt_tokens=%s prefix_tail=%s suffix_head=%s prompt_tail=%s",
            handle.cache_id,
            handle.prefix_len,
            len(suffix_ids),
            suffix_source,
            len(prompt_ids),
            list(handle.prefix_token_ids[-16:]),
            list(suffix_ids[:16]),
            list(prompt_ids[-32:]),
        )
        return prompt_ids

    def _generate_sync(
        self,
        *,
        prompt_ids: Sequence[int],
        sampling_params: object,
        request_timeout: float | None,
    ) -> str:
        timeout = request_timeout if request_timeout is not None else self.request_timeout
        return self._loop_runner.submit(
            _generate_on_loop(
                engine=self.engine,
                inputs=_tokens_prompt(prompt_ids, tokens_prompt_cls=self.tokens_prompt_cls),
                sampling_params=sampling_params,
            ),
            timeout=timeout,
        )

    def _sampling_params(
        self,
        *,
        max_tokens: int,
        generation_config: GenerationConfig,
        stop_sequences: Sequence[str] | None,
        detokenize: bool,
        logprobs: int | None = None,
        allowed_token_ids: Sequence[int] | None = None,
    ):
        sampling_params_cls = self.sampling_params_cls
        if sampling_params_cls is None:
            try:
                from vllm.sampling_params import SamplingParams as ImportedSamplingParams
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("vllm is required for LocalVLLMClient") from exc
            sampling_params_cls = ImportedSamplingParams
        kwargs = {
            "n": 1,
            "max_tokens": max(1, int(max_tokens)),
            "temperature": float(generation_config.temperature),
            "top_p": float(generation_config.top_p),
            "seed": generation_config.seed,
            "stop": list(stop_sequences or []) or None,
            "stop_token_ids": list(self.stop_token_ids) or None,
            "logprobs": logprobs,
            "allowed_token_ids": list(allowed_token_ids) if allowed_token_ids is not None else None,
            "detokenize": bool(detokenize),
            "skip_special_tokens": True,
        }
        return sampling_params_cls(**_filter_callable_kwargs(sampling_params_cls, kwargs))

    def _encode_messages(self, messages: Sequence[Message]) -> tuple[int, ...]:
        tokenizer = self.tokenizer
        if hasattr(tokenizer, "apply_chat_template"):
            token_ids = self._apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
            )
            return tuple(int(token_id) for token_id in token_ids)
        return tuple(self._encode_text(join_messages(messages)))

    def _encode_prefix_messages(self, messages: Sequence[Message]) -> tuple[tuple[int, ...], str]:
        tokenizer = self.tokenizer
        if not hasattr(tokenizer, "apply_chat_template"):
            text = join_messages(messages)
            return tuple(self._encode_text(text)), text
        rendered = str(
            self._apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=False,
            )
        )
        open_prefix = _strip_final_turn_end(rendered, tokenizer=tokenizer)
        token_ids = tuple(self._encode_text(open_prefix))
        LOGGER.debug(
            "local vllm encoded open prefix messages=%s rendered_chars=%s "
            "open_prefix_chars=%s prefix_tokens=%s prefix_text_tail=%r",
            len(tuple(messages)),
            len(rendered),
            len(open_prefix),
            len(token_ids),
            open_prefix[-240:],
        )
        return token_ids, open_prefix

    def _encode_cached_suffix(
        self,
        *,
        messages: Sequence[Message],
        handle: LocalVLLMPrefixCacheHandle,
        suffix_text: str,
    ) -> tuple[int, ...]:
        tokenizer = self.tokenizer
        if not handle.prefix_text or not hasattr(tokenizer, "apply_chat_template"):
            return tuple(self._encode_text(suffix_text))
        full_text = str(
            self._apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        )
        if full_text.startswith(handle.prefix_text):
            suffix_rendered = full_text[len(handle.prefix_text):]
            if not suffix_rendered and str(suffix_text or ""):
                LOGGER.warning(
                    "local vllm rendered chat suffix is empty despite non-empty suffix_text "
                    "cache_id=%s; falling back to raw suffix encode",
                    handle.cache_id,
                )
                return tuple(self._encode_text(suffix_text))
            suffix_ids = tuple(self._encode_text(suffix_rendered))
            LOGGER.debug(
                "local vllm encoded cached suffix from full chat template cache_id=%s "
                "full_chars=%s prefix_chars=%s suffix_chars=%s suffix_text_head=%r",
                handle.cache_id,
                len(full_text),
                len(handle.prefix_text),
                len(suffix_rendered),
                suffix_rendered[:240],
            )
            return suffix_ids
        full_ids = tuple(self._encode_messages(messages))
        if full_ids[: handle.prefix_len] == handle.prefix_token_ids:
            suffix_ids = full_ids[handle.prefix_len:]
            LOGGER.warning(
                "local vllm prefix text mismatch but token prefix matched cache_id=%s "
                "full_tokens=%s prefix_len=%s suffix_tokens=%s",
                handle.cache_id,
                len(full_ids),
                handle.prefix_len,
                len(suffix_ids),
            )
            return suffix_ids
        LOGGER.warning(
            "local vllm prefix cache prompt mismatch cache_id=%s full_chars=%s "
            "prefix_chars=%s full_tokens=%s prefix_len=%s; falling back to raw suffix encode",
            handle.cache_id,
            len(full_text),
            len(handle.prefix_text),
            len(full_ids),
            handle.prefix_len,
        )
        return tuple(self._encode_text(suffix_text))

    def _apply_chat_template(
        self,
        messages: Sequence[Message],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> Any:
        tokenizer = self.tokenizer
        kwargs = {
            "add_generation_prompt": bool(add_generation_prompt),
            "tokenize": bool(tokenize),
            "enable_thinking": False,
            "preserve_thinking": False,
            "add_vision_id": False,
        }
        try:
            return tokenizer.apply_chat_template(list(messages), **kwargs)
        except TypeError:
            fallback = {
                "add_generation_prompt": bool(add_generation_prompt),
                "tokenize": bool(tokenize),
            }
            return tokenizer.apply_chat_template(list(messages), **fallback)

    def _encode_text(self, text: str) -> tuple[int, ...]:
        tokenizer = self.tokenizer
        if hasattr(tokenizer, "encode"):
            return tuple(int(token_id) for token_id in tokenizer.encode(str(text or ""), add_special_tokens=False))
        raise RuntimeError("local vllm tokenizer does not expose encode(...)")

    def close(self) -> None:
        shutdown = getattr(self.engine, "shutdown_background_loop", None)
        if callable(shutdown):
            shutdown()
        shutdown_engine = getattr(getattr(self.engine, "llm_engine", None), "shutdown", None)
        if callable(shutdown_engine):
            shutdown_engine()
        self._loop_runner.close()


async def _generate_on_loop(*, engine: object, inputs: object, sampling_params: object) -> str:
    if _engine_generate_uses_legacy_prompt_list(engine):
        result = engine.generate([inputs], sampling_params, use_tqdm=False)
    else:
        result = engine.generate(
            inputs,
            sampling_params,
            str(uuid.uuid4()),
        )
    if hasattr(result, "__aiter__"):
        return await _collect_async_generation(result)
    if inspect.isawaitable(result):
        return await _await_generation(result)
    return _extract_generation_text(result)


def _engine_generate_uses_legacy_prompt_list(engine: object) -> bool:
    try:
        sig = inspect.signature(getattr(engine, "generate"))
    except (TypeError, ValueError):
        return False
    params = list(sig.parameters.values())
    if len(params) < 3:
        return False
    return params[2].name != "request_id"


async def _collect_async_generation(result: Any) -> str:
    final_output = None
    async for request_output in result:
        final_output = request_output
        if getattr(request_output, "finished", False):
            break
    if final_output is None:
        raise RuntimeError("local vLLM returned no request outputs")
    return _extract_generation_text(final_output)


async def _await_generation(awaitable: Any) -> str:
    result = await awaitable
    return _extract_generation_text(result)


def build_engine_args(async_engine_args_cls: object, **kwargs: Any) -> Any:
    filtered = _filter_callable_kwargs(async_engine_args_cls, kwargs)
    skipped = sorted(set(kwargs).difference(filtered))
    if skipped:
        LOGGER.warning("current vLLM AsyncEngineArgs does not support options; skipped=%s", skipped)
    return async_engine_args_cls(**filtered)


def _filter_callable_kwargs(callable_obj: object, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return dict(kwargs)
    supported = set(sig.parameters.keys())
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in supported}


def _supports_callable_kwarg(callable_obj: object, key: str) -> bool:
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()):
        return True
    return str(key) in sig.parameters


def _tokens_prompt(token_ids: Sequence[int], *, tokens_prompt_cls: object | None = None) -> object:
    payload = [int(token_id) for token_id in token_ids]
    if tokens_prompt_cls is not None:
        return tokens_prompt_cls(prompt_token_ids=payload)
    return {"prompt_token_ids": payload}


def _extract_generation_text(outputs: Any) -> str:
    first = _first_request_output(outputs)
    completion = _first_completion_output(first)
    text = getattr(completion, "text", None)
    if text is None and isinstance(completion, Mapping):
        text = completion.get("text")
    resolved_text = str(text or "")
    raw_summary = _summarize_generation_outputs(first, completion)
    if resolved_text:
        LOGGER.debug("local vllm raw generation output=%s", raw_summary)
    else:
        LOGGER.warning("local vllm generated empty text raw=%s", raw_summary)
    return resolved_text


def _first_request_output(outputs: Any) -> Any:
    if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes, bytearray)) and outputs:
        return outputs[0]
    return outputs


def _first_completion_output(request_output: Any) -> Any:
    completions = getattr(request_output, "outputs", None)
    if completions is None and isinstance(request_output, Mapping):
        completions = request_output.get("outputs")
    if isinstance(completions, Sequence) and not isinstance(completions, (str, bytes, bytearray)) and completions:
        return completions[0]
    raise RuntimeError(f"local vLLM returned no completion outputs: {request_output!r}")


def _summarize_generation_outputs(request_output: Any, completion: Any) -> dict[str, Any]:
    prompt_token_ids = getattr(request_output, "prompt_token_ids", None)
    if prompt_token_ids is None and isinstance(request_output, Mapping):
        prompt_token_ids = request_output.get("prompt_token_ids")
    token_ids = getattr(completion, "token_ids", None)
    if token_ids is None and isinstance(completion, Mapping):
        token_ids = completion.get("token_ids")
    return {
        "request_id": _output_attr(request_output, "request_id"),
        "finished": _output_attr(request_output, "finished"),
        "prompt_tokens": _safe_len(prompt_token_ids),
        "token_ids": _safe_int_list(token_ids),
        "text": _output_attr(completion, "text"),
        "finish_reason": _output_attr(completion, "finish_reason"),
        "stop_reason": _output_attr(completion, "stop_reason"),
        "cumulative_logprob": _output_attr(completion, "cumulative_logprob"),
    }


def _output_attr(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _safe_len(value: Any) -> int | None:
    try:
        return len(value)
    except TypeError:
        return None


def _safe_int_list(value: Any) -> list[int] | None:
    if value is None:
        return None
    try:
        return [int(item) for item in value]
    except TypeError:
        return None


def _strip_final_turn_end(rendered: str, *, tokenizer: object) -> str:
    text = str(rendered or "")
    candidates: list[str] = []
    eos_token = getattr(tokenizer, "eos_token", None)
    if eos_token:
        candidates.append(str(eos_token))
    for token in ("<|im_end|>", "<|endoftext|>"):
        if token not in candidates:
            candidates.append(token)
    for token in candidates:
        index = text.rfind(token)
        if index < 0:
            continue
        tail = text[index + len(token):]
        if tail.strip():
            continue
        return text[:index]
    return text


def _hash_token_ids(token_ids: Sequence[int]) -> str:
    digest = hashlib.sha256()
    for token_id in token_ids:
        digest.update(int(token_id).to_bytes(8, "little", signed=True))
    return digest.hexdigest()


def _tokenizer_fingerprint(tokenizer: object) -> str:
    name_or_path = getattr(tokenizer, "name_or_path", None)
    if name_or_path:
        return str(name_or_path)
    return type(tokenizer).__name__


def _default_stop_token_ids(tokenizer: object, *, qwen_im_end_token_id: object) -> tuple[int, ...]:
    stop_ids: list[int] = []
    eos = getattr(tokenizer, "eos_token_id", None)
    if eos is not None:
        stop_ids.append(int(eos))
    if qwen_im_end_token_id is not None and str(qwen_im_end_token_id).strip():
        stop_ids.append(int(qwen_im_end_token_id))
    return tuple(sorted(set(stop_ids)))


__all__ = ["LocalVLLMClient", "LocalVLLMPrefixCacheHandle", "build_engine_args"]
