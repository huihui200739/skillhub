#!/usr/bin/env python
# -*- coding:utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Mapping

from retrieval.service.models import (
    RetrievalMethod,
    SearchConfig,
    SearchProgressiveConfig,
    SearchProgressiveDisclosureConfig,
    SearchProgressiveGenerationConfig,
    SearchProgressivePrefixCacheConfig,
    SearchProgressiveSelectionConfig,
    SearchProgressiveTraversalConfig,
    SearchProgressiveTrieConfig,
)
from retrieval.service.retriever import Retriever

logger = logging.getLogger("web_demo")

_ASCEND_ENV_SCRIPTS = (
    "/usr/local/Ascend/ascend-toolkit/set_env.sh",
    "/usr/local/Ascend/nnal/atb/set_env.sh",
)

_VLLM_TENSOR_PARALLEL_SIZE = 2
_VLLM_MAX_MODEL_LEN = 20480
_VLLM_ENABLE_EXPERT_PARALLEL = True
_VLLM_ENABLE_PREFIX_CACHING = True
_VLLM_DEVICE = "npu"
_VLLM_DTYPE = ""
_QWEN_IM_END_TOKEN_ID = 151643
_MAX_NEW_TOKENS = 9
_PREFIX_CACHE_MAX_ENTRIES = 128
_PREFIX_CACHE_MAX_SUFFIX_TOKENS = 256
_DEFAULT_COMPACT_BOUNDARY_CODEBOOK = tuple(
    """
    AA AB AC AD AE AF AG AH AI AJ AK AL AM AN AO AP AQ AR AS AT AU AV AW AX AY AZ
    BA BB BC BD BE BF BG BH BI BJ BK BL BM BN BO BP BR BS BT BU BV BW BX BY
    CA CB CC CD CE CF CG CH CI CK CL CM CN CO CP CR CS CT CU CV CW CX CY
    DA DB DC DD DE DF DG DH DI DJ DK DL DM DN DO DP DR DS DT DU DV DW DX DY
    EA EB EC ED EE EF EG EH EI EK EL EM EN EO EP EQ ER ES ET EU EV EW EX EZ
    FA FB FC FD FE FF FG FH FI FK FL FM FN FO FP FR FS FT FU FW FX FY
    GA GB GC GD GE GF GG GH GI GL GM GN GO GP GR GS GT GU GV GW GX GY
    HA HB HC HD HE HF HG HH HI HK HL HM HN HO HP HQ HR HS HT HU HV HW HX HY HZ
    IA IB IC ID IE IF IG IH II IJ IK IL IM IN IO IP IQ IR IS IT IU IV IW IX IZ
    JA JB JC JD JE JI JJ JK JM JO JP JR JS JT JU JV
    KA KB KC KD KE KF KG KH KI KK KL KM KN KO KP KR KS KT KU KV KW KY
    LA LB LC
    """.split()
)


class RetriverTest:
    """Progressive-only retrieval service using retriever-managed local vLLM."""

    def __init__(self) -> None:
        self.retriever: Retriever | None = None
        self.skill_indes_path = ""
        self.model_path = ""
        self.tokenizer_path = ""
        self.served_model_name = ""
        self.default_top_k = 5
        self._load_lock = threading.Lock()
        self._loaded = False

    def load(self) -> None:
        with self._load_lock:
            if self._loaded:
                logger.info("progressive retrieval service load skipped; already loaded")
                return

            load_started = perf_counter()
            logger.info("progressive retrieval service load started")

            currentdir = Path(__file__).resolve().parent
            parentdir = _resolve_parent_dir(currentdir)
            logger.info("service path resolved currentdir=%s parentdir=%s",\ 
                        currentdir, parentdir)

            
            model_object_id = os.environ.get("MODEL_OBJECT_ID")
            model_sfs_path = os.environ.get("MODEL_SFS")
            logger.info(f"model_object_id: {model_object_id}")
            logger.info(f"model_sfs_path:{model_sfs_path}")

            if model_object_id or model_sfs_path:
                parentdir = json.loads(model_sfs_path).get("sfsBasePath") + "/" + model_object_id
            else:
                parentdir = os.path.abspath(os.path.join(currentdir, os.pardir))
            self.model_path = os.path.join(parentdir, "model")
            self.skill_indes_path = os.path.join(parentdir, "data")
            
            
            self.tokenizer_path = _env_text("TOKENIZER_PATH", self.model_path)
            self.served_model_name = _env_text("SERVED_MODEL_NAME", Path(self.model_path).name or self.model_path)
            self.default_top_k = _env_int("TOP_K", 5)
            logger.info(
                "service input paths model=%s tokenizer=%s index=%s served_model=%s top_k=%s",
                self.model_path,
                self.tokenizer_path,
                self.skill_indes_path,
                self.served_model_name,
                self.default_top_k,
            )

            vllm_kwargs = _build_vllm_kwargs()
            logger.info(
                "service vllm kwargs prepared generation_device=%s generation_dtype=%s vllm_kwargs=%s",
                _generation_device(),
                _generation_dtype() or "<default>",
                vllm_kwargs,
            )

            try:
                config_started = perf_counter()
                config = self._build_search_config(
                    model_path=self.model_path,
                    tokenizer_path=self.tokenizer_path,
                    served_model_name=self.served_model_name,
                    vllm_kwargs=vllm_kwargs,
                )
                logger.info(
                    "service search config built elapsed_ms=%.3f method=%s generation_backend=%s prefix_cache=%s",
                    (perf_counter() - config_started) * 1000.0,
                    config.method,
                    config.progressive.generation.progressive_generation_backend if config.progressive else "",
                    config.progressive.prefix_cache.progressive_prefix_cache_enabled if config.progressive else False,
                )
                from_index_started = perf_counter()
                logger.info("service loading retriever index start index=%s", self.skill_indes_path)
                self.retriever = Retriever.from_index(
                    self.skill_indes_path,
                    config=config,
                    llm_openai_client=None,
                    llm_model=self.served_model_name,
                )
                logger.info(
                    "service loading retriever index complete elapsed_ms=%.3f",
                    (perf_counter() - from_index_started) * 1000.0,
                )
                warmup_started = perf_counter()
                logger.info("service progressive runtime warmup start")
                self._warmup_progressive_runtime()
                logger.info("service progressive runtime warmup complete elapsed_ms=%.3f",\ 
                            (perf_counter() - warmup_started) * 1000.0)
                self._loaded = True
                logger.info("progressive retrieval service loaded elapsed_ms=%.3f",\ 
                            (perf_counter() - load_started) * 1000.0)
            except Exception:
                logger.exception("progressive retrieval service load failed elapsed_ms=%.3f",\ 
                                 (perf_counter() - load_started) * 1000.0)
                self._cleanup_after_failed_load()
                raise

    def calc(self, req_data: Mapping[str, Any] | None) -> str:
        data = req_data.get("data", {})
        request = dict(data) if isinstance(data, dict) else {}        
        query = str(request.get("query", "查天气")).strip()
        if not query:
            logger.info("service calc skipped because query is empty")
            return json.dumps([], ensure_ascii=False)
        if not self._loaded:
            self.load()
        if self.retriever is None:
            raise RuntimeError("retriever service is not loaded")
        requested_top_k = _coerce_optional_int(request.get("top_k"))
        if requested_top_k is None:
            requested_top_k = _coerce_optional_int(request.get("topk"))
        resolved_top_k = self.default_top_k if requested_top_k is None else max(1, requested_top_k)
        if resolved_top_k > self.default_top_k:
            logger.warning(
                "requested top_k=%s exceeds initialized top_k=%s; returning at most initialized results",
                resolved_top_k,
                self.default_top_k,
            )
            resolved_top_k = self.default_top_k
        names = list(self.retriever.search(query))[:resolved_top_k]
        return json.dumps(names, ensure_ascii=False)

    def close(self) -> None:
        retriever = self.retriever
        if retriever is not None:
            close = getattr(retriever, "close", None)
            if callable(close):
                close()
        self.retriever = None
        self._loaded = False

    def _build_search_config(
        self,
        *,
        model_path: str,
        tokenizer_path: str,
        served_model_name: str,
        vllm_kwargs: Dict[str, Any],
    ) -> SearchConfig:
        codebook = _env_tuple("PROGRESSIVE_COMPACT_BOUNDARY_CODEBOOK", _DEFAULT_COMPACT_BOUNDARY_CODEBOOK)
        compact_code_max_tokens = _compact_code_generation_max_tokens(self.default_top_k)
        prefix_max_new_tokens = max(compact_code_max_tokens, _env_int("PREFIX_CACHE_MAX_NEW_TOKENS", _MAX_NEW_TOKENS))
        progressive_max_tokens = _env_int("PROGRESSIVE_MAX_TOKENS", 96)
        branch_max_tokens = _env_int("PROGRESSIVE_BRANCH_MAX_TOKENS", 96)
        item_max_tokens = _env_int("PROGRESSIVE_ITEM_MAX_TOKENS", 128)
        prefix_max_suffix_tokens = max(1, _env_int("PREFIX_CACHE_MAX_SUFFIX_TOKENS", _PREFIX_CACHE_MAX_SUFFIX_TOKENS))
        return SearchConfig(
            top_k=self.default_top_k,
            method=RetrievalMethod.PROGRESSIVE,
            llm_top_k=_env_int("LLM_TOP_K", self.default_top_k),
            progressive=SearchProgressiveConfig(
                traversal=SearchProgressiveTraversalConfig(
                    progressive_batch_size=_env_int("PROGRESSIVE_BATCH_SIZE", 5),
                    progressive_max_tokens=progressive_max_tokens,
                    progressive_request_timeout=_env_float_optional("PROGRESSIVE_REQUEST_TIMEOUT"),
                    progressive_max_branch_choices=_env_int("PROGRESSIVE_MAX_BRANCH_CHOICES", 6),
                    progressive_auto_expand_child_threshold=_env_int("PROGRESSIVE_AUTO_EXPAND_CHILD_THRESHOLD", 3),
                    progressive_collapse_single_chain=_env_bool("PROGRESSIVE_COLLAPSE_SINGLE_CHAIN", True),
                    progressive_max_collapse_steps=_env_int("PROGRESSIVE_MAX_COLLAPSE_STEPS", 8),
                    progressive_max_parallel_branches=_env_int("PROGRESSIVE_MAX_PARALLEL_BRANCHES", 3),
                    progressive_enable_parallel_branches=_env_bool("PROGRESSIVE_ENABLE_PARALLEL_BRANCHES", True),
                    progressive_auto_terminal_item_threshold=_env_int("PROGRESSIVE_AUTO_TERMINAL_ITEM_THRESHOLD", 12),
                    progressive_branch_choice_slack=_env_int("PROGRESSIVE_BRANCH_CHOICE_SLACK", 2),
                    progressive_branch_candidate_slack=_env_int("PROGRESSIVE_BRANCH_CANDIDATE_SLACK", 1),
                    progressive_round_robin_branch_reduce=_env_bool("PROGRESSIVE_ROUND_ROBIN_BRANCH_REDUCE", True),
                    progressive_branch_max_tokens=branch_max_tokens,
                    progressive_item_max_tokens=item_max_tokens,
                ),
                disclosure=SearchProgressiveDisclosureConfig(
                    progressive_compact_boundary_codes_enabled=\ 
                    _env_bool("PROGRESSIVE_COMPACT_BOUNDARY_CODES_ENABLED", True),
                    progressive_compact_boundary_codebook=codebook,
                    progressive_flatten_full_tree_in_prompt=_env_bool("PROGRESSIVE_FLATTEN_FULL_TREE_IN_PROMPT", True),
                    progressive_max_exposure_depth_per_call=_env_int("PROGRESSIVE_MAX_EXPOSURE_DEPTH_PER_CALL", 99),
                    progressive_exposure_threshold=_env_int("PROGRESSIVE_EXPOSURE_THRESHOLD", 1_000_000_000),
                    progressive_force_expand_single_child=_env_bool("PROGRESSIVE_FORCE_EXPAND_SINGLE_CHILD", True),
                ),
                selection=SearchProgressiveSelectionConfig(
                    progressive_single_forward_logit_selection_enabled=False,
                    progressive_selection_mode="generate",
                ),
                trie=SearchProgressiveTrieConfig(
                    trie_constrained_decoding_enabled=True,
                    trie_constraint_allow_user_nodes=False,
                    trie_constraint_max_candidates=512,
                    trie_constraint_fallback_payload="User.Chat",
                ),
                scoring=None,
                generation=SearchProgressiveGenerationConfig(
                    progressive_generation_backend="vllm",
                    progressive_generation_model_path=model_path,
                    progressive_generation_tokenizer_path=tokenizer_path,
                    progressive_generation_device=_generation_device(),
                    progressive_generation_dtype=_generation_dtype(),
                    progressive_generation_tp_size=_env_int("TENSOR_PARALLEL_SIZE", _VLLM_TENSOR_PARALLEL_SIZE),
                    progressive_generation_dp_size=1,
                    progressive_generation_device_ids=(),
                    progressive_generation_vllm_kwargs={
                        **vllm_kwargs,
                        "request_model": served_model_name,
                    },
                ),
                prefix_cache=SearchProgressivePrefixCacheConfig(
                    progressive_prefix_cache_enabled=True,
                    progressive_prefix_cache_warmup="eager",
                    progressive_prefix_cache_max_entries=\ 
                    _env_int("PREFIX_CACHE_MAX_ENTRIES", _PREFIX_CACHE_MAX_ENTRIES),
                    progressive_prefix_cache_request_pool_size=1,
                    progressive_prefix_cache_max_suffix_tokens=prefix_max_suffix_tokens,
                    progressive_prefix_cache_max_new_tokens=prefix_max_new_tokens,
                    progressive_prefix_cache_on_pool_exhausted="reject",
                    progressive_prefix_cache_on_query_too_long="reject",
                    progressive_prefix_cache_slot_acquire_timeout_ms=0.0,
                ),
            ),
        )

    def _warmup_progressive_runtime(self) -> None:
        if self.retriever is None:
            raise RuntimeError("retriever is not initialized")
        runtime_config = getattr(self.retriever, "_config", None)
        if runtime_config is None:
            raise RuntimeError("retriever runtime config is unavailable")
        progressive = getattr(runtime_config, "progressive", None)
        logger.info(
            "service warmup runtime config method=%s top_k=%s generation_backend=%s model=%s tokenizer=%s",
            getattr(runtime_config, "method", ""),
            getattr(runtime_config, "top_k", ""),
            getattr(progressive, "generation_backend", "") if progressive is not None else "",
            getattr(progressive, "generation_model_path", "") if progressive is not None else "",
            getattr(progressive, "generation_tokenizer_path", "") if progressive is not None else "",
        )
        get_client = getattr(self.retriever, "_get_progressive_client", None)
        if not callable(get_client):
            raise RuntimeError("retriever does not expose progressive runtime initializer")
        client_started = perf_counter()
        client = get_client(runtime_config)
        logger.info(
            "service progressive client ready elapsed_ms=%.3f client_type=%s",
            (perf_counter() - client_started) * 1000.0,
            type(client).__name__,
        )
        get_progressive_retriever = getattr(self.retriever, "_get_progressive_retriever", None)
        loaded_index = getattr(self.retriever, "_loaded_index", None)
        root = getattr(loaded_index, "tree_root", None)
        if callable(get_progressive_retriever) and root is not None:
            retriever_started = perf_counter()
            get_progressive_retriever(
                progressive_client=client,
                runtime_config=runtime_config,
                root=root,
            )
            logger.info("service progressive retriever prepared elapsed_ms=%.3f", (perf_counter() - retriever_started) * 1000.0)

    def _cleanup_after_failed_load(self) -> None:
        retriever = self.retriever
        self.retriever = None
        self._loaded = False
        if retriever is None:
            return
        try:
            close = getattr(retriever, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.exception("failed to close retriever after load failure")


def _resolve_parent_dir(currentdir: Path) -> Path:
    model_object_id = os.environ.get("MODEL_OBJECT_ID")
    model_sfs_path = os.environ.get("MODEL_SFS")
    logger.info("model_object_id: %s", model_object_id)
    logger.info("model_sfs_path: %s", model_sfs_path)
    if model_object_id and model_sfs_path:
        try:
            sfs_base = json.loads(model_sfs_path).get("sfsBasePath")
            if sfs_base:
                return Path(str(sfs_base)) / str(model_object_id)
        except Exception:
            logger.exception("failed to parse MODEL_SFS; falling back to repository parent")
    return currentdir.parent


def _parse_null_separated_env(raw: bytes) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        try:
            name = key.decode()
            text = value.decode()
        except UnicodeDecodeError:
            name = key.decode(errors="replace")
            text = value.decode(errors="replace")
        if name:
            env[name] = text
    return env


def _apply_environment_updates(updates: Mapping[str, str]) -> list[str]:
    changed: list[str] = []
    for key, value in updates.items():
        old_value = os.environ.get(key)
        if old_value == value:
            continue
        os.environ[key] = value
        changed.append(key)
    changed.sort()
    return changed


def _build_vllm_kwargs() -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "tensor_parallel_size": _env_int("TENSOR_PARALLEL_SIZE", _VLLM_TENSOR_PARALLEL_SIZE),
        "max_model_len": _env_int("MAX_MODEL_LEN", _VLLM_MAX_MODEL_LEN),
        "enable_expert_parallel": _env_bool("ENABLE_EXPERT_PARALLEL", _VLLM_ENABLE_EXPERT_PARALLEL),
        "enable_prefix_caching": _env_bool("ENABLE_PREFIX_CACHING", _VLLM_ENABLE_PREFIX_CACHING),
        "trust_remote_code": _env_bool("TRUST_REMOTE_CODE", True),
        "qwen_im_end_token_id": _env_int("QWEN_IM_END_ID", _QWEN_IM_END_TOKEN_ID),
    }
    gpu_memory_utilization = _env_float_optional("GPU_MEMORY_UTILIZATION")
    if gpu_memory_utilization is not None:
        kwargs["gpu_memory_utilization"] = gpu_memory_utilization
    extra_json = os.environ.get("VLLM_KWARGS_JSON")
    if extra_json:
        extra = json.loads(extra_json)
        if not isinstance(extra, dict):
            raise ValueError("VLLM_KWARGS_JSON must decode to an object")
        kwargs.update(extra)
    return kwargs


def _env_text(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return str(default)
    return str(value).strip()


def _generation_device() -> str:
    return _env_text("GENERATION_DEVICE", _env_text("VLLM_DEVICE", _VLLM_DEVICE))


def _generation_dtype() -> str:
    return _env_text("GENERATION_DTYPE", _env_text("VLLM_DTYPE", _VLLM_DTYPE))


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return int(default)
    return int(value)


def _clamp_generation_tokens(name: str, *, requested: int, limit: int) -> int:
    requested_value = max(1, int(requested))
    limit_value = max(1, int(limit))
    if requested_value <= limit_value:
        return requested_value
    logger.warning(
        "%s=%s exceeds PREFIX_CACHE_MAX_NEW_TOKENS=%s; clamped to client generation budget",
        name,
        requested_value,
        limit_value,
    )
    return limit_value


def _env_float_optional(name: str) -> float | None:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return None
    return float(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_tuple(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return tuple(default)
    text = raw.strip()
    if text.startswith("["):
        values = json.loads(text)
        if not isinstance(values, list):
            raise ValueError(f"{name} must be a JSON list or comma-separated string")
        return tuple(str(item).strip() for item in values if str(item).strip())
    return tuple(item.strip() for item in text.split(",") if item.strip())


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or not str(value).strip():
        return None
    return int(value)


def _compact_code_generation_max_tokens(top_k: int) -> int:
    return max(1, 2 * max(1, int(top_k)) - 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = RetriverTest()
    try:
        service.load()
        service.calc({})
    finally:
        service.close()
