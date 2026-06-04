"""Unit tests for retrieval.service — covers defaults, models, methods, and retriever."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence
from unittest.mock import MagicMock, patch

from models.retrieval import RetrieverCandidate, RetrieverChoice, RetrieverNode, RetrieverItem, RetrieverTrace
from retrieval.io.loading import CatalogRecord, LoadedRetrieverIndex
from retrieval.llm import LLMClientCapabilities, ProgressiveLLMClient
from retrieval.service.defaults import normalize_method, serialize_hit_summary, serialize_trace_event
from retrieval.service.methods import (
    AutoRetrievalMethod,
    BaseRetrievalMethod,
    ProgressiveRetrievalMethod,
    RetrievalMethodContext,
    RetrievalRequest,
    create_retrieval_method,
    truncate_primary_result,
)
from retrieval.service.models import (
    RetrievalMethod,
    RetrieverConfig,
    RetrieverSearchResult,
    SearchConfig,
    SearchProgressiveConfig,
    SearchProgressiveDisclosureConfig,
    SearchProgressiveGenerationConfig,
    SearchProgressivePrefixCacheConfig,
    SearchProgressiveScoringConfig,
    SearchProgressiveSelectionConfig,
    SearchProgressiveTraversalConfig,
    SearchProgressiveTrieConfig,
    SearchRequestConfig,
    runtime_retriever_config_from_search,
)
from retrieval.service.retriever import (
    Retriever,
    _coerce_llm_client,
    _coerce_retriever_config,
    _prefix_cached_generation_configured,
    _prefix_cached_generation_requested,
    _progressive_fixed_prefix_cache_requested,
    _progressive_model_name,
    _progressive_runtime_log_identity,
    _progressive_search_backend_name,
    _resolve_request_top_k,
    _validate_search_request_config,
)
from retrieval.tree.types import ProgressiveRetrieverConfig


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_loaded_index(
    *,
    catalog_records: Sequence[CatalogRecord] = (),
    tree_root: RetrieverNode | None = None,
) -> LoadedRetrieverIndex:
    return LoadedRetrieverIndex(
        index_dir="/tmp/fake",
        tree_root=tree_root or RetrieverNode(node_id="ROOT", label="ROOT"),
        choices=tuple(
            RetrieverChoice(choice_id=r.choice_id, payload=r.payload, description=r.description)
            for r in catalog_records
        ),
        catalog_records=tuple(catalog_records),
    )


def _make_llm_client(*, completion: bool = True) -> MagicMock:
    client = MagicMock(spec=ProgressiveLLMClient)
    client.capabilities = LLMClientCapabilities(
        completion=completion,
        streaming=False,
        candidate_scoring=False,
    )
    client.name = "mock"
    return client


def _private_attr(obj: Any, name: str) -> Any:
    return getattr(obj, name)


def _private_call(obj: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(obj, name)(*args, **kwargs)


def _sample_result(
    *,
    method: str = "progressive",
    n: int = 3,
    elapsed_ms: float = 10.0,
) -> RetrieverSearchResult:
    records = [
        {
            "rank": i,
            "raw_output": f"choice_{i}",
            "resolved_payload": f"payload_{i}",
            "valid": True,
            "selected": i == 1,
            "choice_id": f"choice_{i}",
            "description": f"desc_{i}",
            "score": 1.0 - i * 0.1,
            "source": "progressive",
        }
        for i in range(1, n + 1)
    ]
    return RetrieverSearchResult(
        method=method,
        payloads=[r["resolved_payload"] for r in records],
        candidate_records=records,
        summary_lines=[f"{i}. payload_{i}" for i in range(1, n + 1)],
        selected_payload="payload_1",
        selected_rank=1,
        elapsed_ms=elapsed_ms,
    )


# ── defaults.py ───────────────────────────────────────────────────────────


class NormalizeMethodTests(unittest.TestCase):
    def test_enum_auto(self) -> None:
        self.assertEqual(normalize_method(RetrievalMethod.AUTO), "auto")

    def test_enum_progressive(self) -> None:
        self.assertEqual(normalize_method(RetrievalMethod.PROGRESSIVE), "progressive")

    def test_string_auto(self) -> None:
        self.assertEqual(normalize_method("auto"), "auto")

    def test_string_progressive(self) -> None:
        self.assertEqual(normalize_method("progressive"), "progressive")

    def test_none_returns_auto(self) -> None:
        self.assertEqual(normalize_method(None), "auto")

    def test_whitespace_string(self) -> None:
        self.assertEqual(normalize_method("  auto  "), "auto")

    def test_uppercase_string(self) -> None:
        self.assertEqual(normalize_method("PROGRESSIVE"), "progressive")

    def test_unknown_string_falls_back_to_auto(self) -> None:
        self.assertEqual(normalize_method("hybrid"), "auto")

    def test_empty_string_falls_back_to_auto(self) -> None:
        self.assertEqual(normalize_method(""), "auto")


class SerializeTraceEventTests(unittest.TestCase):
    def test_normal_event(self) -> None:
        from models.retrieval import RetrieverTraceEvent

        event = RetrieverTraceEvent(event_type="select", node_id="node.1", depth=2, detail={"key": "val"})
        result = serialize_trace_event(event)
        self.assertEqual(result["event_type"], "select")
        self.assertEqual(result["node_id"], "node.1")
        self.assertEqual(result["depth"], 2)
        self.assertEqual(result["detail"], {"key": "val"})

    def test_none_detail_produces_empty_dict(self) -> None:
        from models.retrieval import RetrieverTraceEvent

        event = RetrieverTraceEvent(event_type="expand", node_id="n", depth=0, detail=None)  # type: ignore[arg-type]
        result = serialize_trace_event(event)
        self.assertEqual(result["detail"], {})


class SerializeHitSummaryTests(unittest.TestCase):
    def test_normal_values(self) -> None:
        result = serialize_hit_summary("id1", "payload_a", 3, 0.95)
        self.assertEqual(result["choice_id"], "id1")
        self.assertEqual(result["payload"], "payload_a")
        self.assertEqual(result["rank"], 3)
        self.assertAlmostEqual(result["score"], 0.95)

    def test_type_coercion(self) -> None:
        result = serialize_hit_summary(42, 99, "5", "0.1")
        self.assertIsInstance(result["choice_id"], str)
        self.assertIsInstance(result["payload"], str)
        self.assertIsInstance(result["rank"], int)
        self.assertIsInstance(result["score"], float)


# ── models.py ─────────────────────────────────────────────────────────────


class RetrievalMethodTests(unittest.TestCase):
    def test_auto_value(self) -> None:
        self.assertEqual(RetrievalMethod.AUTO.value, "auto")

    def test_progressive_value(self) -> None:
        self.assertEqual(RetrievalMethod.PROGRESSIVE.value, "progressive")

    def test_is_string_enum(self) -> None:
        self.assertIsInstance(RetrievalMethod.AUTO, str)


class SearchConfigDefaultsTests(unittest.TestCase):
    def test_search_config_requires_top_k(self) -> None:
        cfg = SearchConfig(top_k=5)
        self.assertEqual(cfg.top_k, 5)
        self.assertEqual(cfg.method, RetrievalMethod.AUTO)
        self.assertIsNone(cfg.llm_top_k)
        self.assertIsNone(cfg.progressive)

    def test_search_request_config_defaults(self) -> None:
        cfg = SearchRequestConfig()
        self.assertIsNone(cfg.top_k)

    def test_retriever_config_defaults(self) -> None:
        cfg = RetrieverConfig()
        self.assertEqual(cfg.method, "auto")
        self.assertEqual(cfg.top_k, 10)
        self.assertIsNone(cfg.llm_top_k)
        self.assertIsInstance(cfg.progressive, ProgressiveRetrieverConfig)

    def test_frozen_dataclass_immutability(self) -> None:
        cfg = SearchConfig(top_k=5)
        with self.assertRaises(AttributeError):
            cfg.top_k = 10  # type: ignore[misc]


class RetrieverSearchResultTests(unittest.TestCase):
    def test_construction(self) -> None:
        result = RetrieverSearchResult(
            method="progressive",
            payloads=["a"],
            candidate_records=[{"rank": 1}],
            summary_lines=["1. a"],
            selected_payload="a",
            selected_rank=1,
        )
        self.assertEqual(result.method, "progressive")
        self.assertEqual(result.elapsed_ms, 0.0)
        self.assertEqual(result.trace_events, [])

    def test_mutable_default_fields(self) -> None:
        result = RetrieverSearchResult(
            method="auto", payloads=[], candidate_records=[], summary_lines=[],
            selected_payload=None, selected_rank=-1,
        )
        result.trace_events.append({"event": "x"})
        self.assertEqual(len(result.trace_events), 1)


class RuntimeRetrieverConfigFromSearchTests(unittest.TestCase):
    def test_default_search_config(self) -> None:
        cfg = SearchConfig(top_k=5)
        result = runtime_retriever_config_from_search(cfg)
        self.assertIsInstance(result, RetrieverConfig)
        self.assertEqual(result.method, "auto")
        self.assertEqual(result.top_k, 5)
        self.assertIsNone(result.llm_top_k)
        self.assertIsInstance(result.progressive, ProgressiveRetrieverConfig)

    def test_top_k_clamped_to_minimum_1(self) -> None:
        cfg = SearchConfig(top_k=0)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.top_k, 1)

    def test_negative_top_k_clamped(self) -> None:
        cfg = SearchConfig(top_k=-3)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.top_k, 1)

    def test_llm_top_k_none_passes_through(self) -> None:
        cfg = SearchConfig(top_k=5, llm_top_k=None)
        result = runtime_retriever_config_from_search(cfg)
        self.assertIsNone(result.llm_top_k)

    def test_llm_top_k_positive(self) -> None:
        cfg = SearchConfig(top_k=5, llm_top_k=3)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.llm_top_k, 3)

    def test_llm_top_k_zero(self) -> None:
        cfg = SearchConfig(top_k=5, llm_top_k=0)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.llm_top_k, 0)

    def test_none_progressive_fills_defaults(self) -> None:
        cfg = SearchConfig(top_k=5, progressive=None)
        result = runtime_retriever_config_from_search(cfg)
        self.assertIsInstance(result.progressive, ProgressiveRetrieverConfig)
        # Should have default values
        self.assertEqual(result.progressive.top_k, 5)

    def test_partial_sub_configs_fill_defaults(self) -> None:
        progressive = SearchProgressiveConfig(
            traversal=SearchProgressiveTraversalConfig(progressive_batch_size=4),
            trie=None,
            scoring=None,
            generation=None,
            prefix_cache=None,
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.progressive.batch_size, 4)
        # trie defaults should be applied
        self.assertFalse(result.progressive.trie_constrained_decoding_enabled)

    def test_custom_traversal_values_pass_through(self) -> None:
        progressive = SearchProgressiveConfig(
            traversal=SearchProgressiveTraversalConfig(
                progressive_max_tokens=100,
                progressive_max_branch_choices=8,
                progressive_collapse_single_chain=False,
            ),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.progressive.max_tokens, 100)
        self.assertEqual(result.progressive.max_branch_choices, 8)
        self.assertFalse(result.progressive.collapse_single_chain)

    def test_custom_disclosure_values(self) -> None:
        progressive = SearchProgressiveConfig(
            disclosure=SearchProgressiveDisclosureConfig(
                progressive_compact_boundary_codes_enabled=True,
                progressive_max_exposure_depth_per_call=4,
            ),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertTrue(result.progressive.compact_boundary_codes_enabled)
        self.assertEqual(result.progressive.max_exposure_depth_per_call, 4)

    def test_custom_selection_values(self) -> None:
        progressive = SearchProgressiveConfig(
            selection=SearchProgressiveSelectionConfig(
                progressive_single_forward_logit_selection_enabled=True,
                progressive_selection_mode="logit_selection",
            ),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertTrue(result.progressive.single_forward_logit_selection_enabled)
        self.assertEqual(result.progressive.selection_mode, "logit_selection")

    def test_custom_scoring_values(self) -> None:
        progressive = SearchProgressiveConfig(
            scoring=SearchProgressiveScoringConfig(
                progressive_scoring_backend="vllm",
                progressive_scoring_backend_model_path="/models/test",
                progressive_scoring_fallback_mode="generate",
            ),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.progressive.scoring_backend, "vllm")
        self.assertEqual(result.progressive.scoring_backend_model_path, "/models/test")
        self.assertEqual(result.progressive.scoring_fallback_mode, "generate")

    def test_custom_generation_values(self) -> None:
        progressive = SearchProgressiveConfig(
            generation=SearchProgressiveGenerationConfig(
                progressive_generation_backend="transformers_prefix_cached",
                progressive_generation_model_path="/models/gen",
                progressive_generation_tp_size=2,
            ),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.progressive.generation_backend, "transformers_prefix_cached")
        self.assertEqual(result.progressive.generation_model_path, "/models/gen")
        self.assertEqual(result.progressive.generation_tp_size, 2)

    def test_custom_prefix_cache_values(self) -> None:
        progressive = SearchProgressiveConfig(
            prefix_cache=SearchProgressivePrefixCacheConfig(
                progressive_prefix_cache_enabled=True,
                progressive_prefix_cache_max_entries=256,
            ),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertTrue(result.progressive.prefix_cache_enabled)
        self.assertEqual(result.progressive.prefix_cache_max_entries, 256)

    def test_boundary_values_clamped(self) -> None:
        progressive = SearchProgressiveConfig(
            traversal=SearchProgressiveTraversalConfig(
                progressive_batch_size=0,
                progressive_max_tokens=0,
                progressive_max_branch_choices=0,
            ),
        )
        cfg = SearchConfig(top_k=0, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.top_k, 1)
        self.assertEqual(result.progressive.batch_size, 1)
        self.assertEqual(result.progressive.max_tokens, 1)
        self.assertEqual(result.progressive.max_branch_choices, 1)

    def test_method_value_extracted(self) -> None:
        cfg = SearchConfig(top_k=5, method=RetrievalMethod.PROGRESSIVE)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.method, "progressive")

    def test_empty_selection_mode_defaults_to_generate(self) -> None:
        progressive = SearchProgressiveConfig(
            selection=SearchProgressiveSelectionConfig(progressive_selection_mode=""),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.progressive.selection_mode, "generate")

    def test_whitespace_selection_mode_stripped(self) -> None:
        progressive = SearchProgressiveConfig(
            selection=SearchProgressiveSelectionConfig(progressive_selection_mode="  GENERATE  "),
        )
        cfg = SearchConfig(top_k=5, progressive=progressive)
        result = runtime_retriever_config_from_search(cfg)
        self.assertEqual(result.progressive.selection_mode, "generate")


# ── methods.py ────────────────────────────────────────────────────────────


class RetrievalRequestTests(unittest.TestCase):
    def test_construction(self) -> None:
        req = RetrievalRequest(query="test", top_k=5, runtime_config=RetrieverConfig())
        self.assertEqual(req.query, "test")
        self.assertEqual(req.top_k, 5)
        self.assertIsNone(req.llm_top_k)

    def test_multimodal_query(self) -> None:
        query: list[dict[str, str]] = [{"role": "user", "content": "hello"}]
        req = RetrievalRequest(query=query, top_k=3, runtime_config=RetrieverConfig())
        self.assertEqual(len(req.query), 1)


class ProgressiveRetrievalMethodTests(unittest.TestCase):
    def _make_context(self, *, unavailable_reason: str | None = None) -> RetrievalMethodContext:
        ctx = MagicMock()
        ctx.progressive_unavailable_reason = MagicMock(return_value=unavailable_reason)
        ctx.search_progressive = MagicMock(return_value=_sample_result())
        ctx.emit_fallback_event = MagicMock()
        return ctx

    def test_search_delegates_when_available(self) -> None:
        ctx = self._make_context(unavailable_reason=None)
        method = ProgressiveRetrievalMethod(context=ctx)
        req = RetrievalRequest(query="q", top_k=5, runtime_config=RetrieverConfig())
        result = method.search(req)
        self.assertIsInstance(result, RetrieverSearchResult)
        ctx.search_progressive.assert_called_once()

    def test_search_raises_when_unavailable(self) -> None:
        ctx = self._make_context(unavailable_reason="no llm")
        method = ProgressiveRetrievalMethod(context=ctx)
        req = RetrievalRequest(query="q", top_k=5, runtime_config=RetrieverConfig())
        with self.assertRaises(RuntimeError) as cm:
            method.search(req)
        self.assertIn("no llm", str(cm.exception))

    def test_method_name(self) -> None:
        ctx = self._make_context()
        method = ProgressiveRetrievalMethod(context=ctx)
        self.assertEqual(method.method_name, "progressive")


class AutoRetrievalMethodTests(unittest.TestCase):
    def _make_context(self, *, unavailable_reason: str | None = None) -> RetrievalMethodContext:
        ctx = MagicMock()
        ctx.progressive_unavailable_reason = MagicMock(return_value=unavailable_reason)
        ctx.search_progressive = MagicMock(return_value=_sample_result())
        ctx.emit_fallback_event = MagicMock()
        return ctx

    def test_inherits_progressive(self) -> None:
        self.assertTrue(issubclass(AutoRetrievalMethod, ProgressiveRetrievalMethod))

    def test_method_name_is_auto(self) -> None:
        ctx = self._make_context()
        method = AutoRetrievalMethod(context=ctx)
        self.assertEqual(method.method_name, "auto")

    def test_search_delegates_when_available(self) -> None:
        ctx = self._make_context(unavailable_reason=None)
        method = AutoRetrievalMethod(context=ctx)
        req = RetrievalRequest(query="q", top_k=5, runtime_config=RetrieverConfig())
        result = method.search(req)
        self.assertIsInstance(result, RetrieverSearchResult)


class CreateRetrievalMethodTests(unittest.TestCase):
    def test_returns_progressive_method(self) -> None:
        ctx = MagicMock()
        method = create_retrieval_method("progressive", context=ctx)
        self.assertIsInstance(method, ProgressiveRetrievalMethod)

    def test_returns_progressive_for_auto(self) -> None:
        ctx = MagicMock()
        method = create_retrieval_method("auto", context=ctx)
        self.assertIsInstance(method, ProgressiveRetrievalMethod)


class TruncatePrimaryResultTests(unittest.TestCase):
    def test_llm_top_k_none_uses_top_k(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        self.assertEqual(len(truncated.candidate_records), 2)
        self.assertEqual(len(truncated.payloads), 2)

    def test_llm_top_k_smaller_than_top_k(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=5, llm_top_k=2)
        self.assertEqual(len(truncated.candidate_records), 2)

    def test_llm_top_k_larger_than_top_k(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=10)
        self.assertEqual(len(truncated.candidate_records), 2)

    def test_limit_exceeds_record_count_returns_unchanged(self) -> None:
        result = _sample_result(n=3)
        truncated = truncate_primary_result(result, top_k=10, llm_top_k=None)
        self.assertEqual(len(truncated.candidate_records), 3)

    def test_truncation_reranks(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        ranks = [r["rank"] for r in truncated.candidate_records]
        self.assertEqual(ranks, [1, 2])

    def test_truncation_sets_selected(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        self.assertTrue(truncated.candidate_records[0]["selected"])
        self.assertFalse(truncated.candidate_records[1]["selected"])

    def test_truncation_selected_payload_and_rank(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        self.assertEqual(truncated.selected_payload, "payload_1")
        self.assertEqual(truncated.selected_rank, 1)

    def test_empty_records(self) -> None:
        result = RetrieverSearchResult(
            method="progressive", payloads=[], candidate_records=[],
            summary_lines=[], selected_payload=None, selected_rank=-1,
        )
        truncated = truncate_primary_result(result, top_k=5, llm_top_k=None)
        self.assertEqual(len(truncated.candidate_records), 0)
        self.assertIsNone(truncated.selected_payload)

    def test_single_record_no_truncation(self) -> None:
        result = _sample_result(n=1)
        truncated = truncate_primary_result(result, top_k=5, llm_top_k=None)
        self.assertEqual(len(truncated.candidate_records), 1)

    def test_summary_lines_truncated(self) -> None:
        result = _sample_result(n=5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        self.assertEqual(len(truncated.summary_lines), 2)

    def test_trace_events_preserved(self) -> None:
        result = _sample_result(n=5)
        result.trace_events = [{"event": "x"}]
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        self.assertEqual(len(truncated.trace_events), 1)

    def test_elapsed_ms_preserved(self) -> None:
        result = _sample_result(n=5, elapsed_ms=42.5)
        truncated = truncate_primary_result(result, top_k=2, llm_top_k=None)
        self.assertAlmostEqual(truncated.elapsed_ms, 42.5)


# ── retriever.py (module-level helpers) ───────────────────────────────────


class CoerceRetrieverConfigTests(unittest.TestCase):
    def test_none_returns_default(self) -> None:
        result = _coerce_retriever_config(None)
        self.assertIsInstance(result, RetrieverConfig)
        self.assertEqual(result.method, "auto")

    def test_retriever_config_passthrough(self) -> None:
        cfg = RetrieverConfig(method="progressive", top_k=3)
        result = _coerce_retriever_config(cfg)
        self.assertIs(result, cfg)

    def test_search_config_converted(self) -> None:
        cfg = SearchConfig(top_k=5, method=RetrievalMethod.PROGRESSIVE)
        result = _coerce_retriever_config(cfg)
        self.assertIsInstance(result, RetrieverConfig)
        self.assertEqual(result.method, "progressive")
        self.assertEqual(result.top_k, 5)

    def test_invalid_type_raises(self) -> None:
        with self.assertRaises(TypeError) as cm:
            _coerce_retriever_config("invalid")  # type: ignore[arg-type]
        self.assertIn("Unsupported", str(cm.exception))


class ResolveRequestTopKTests(unittest.TestCase):
    def test_none_search_config_uses_runtime(self) -> None:
        runtime = RetrieverConfig(top_k=10)
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=None), 10)

    def test_search_config_top_k_overrides(self) -> None:
        runtime = RetrieverConfig(top_k=10)
        search_cfg = SearchRequestConfig(top_k=3)
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=search_cfg), 3)

    def test_search_config_top_k_none_uses_runtime(self) -> None:
        runtime = RetrieverConfig(top_k=7)
        search_cfg = SearchRequestConfig(top_k=None)
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=search_cfg), 7)

    def test_top_k_clamped_to_minimum_1(self) -> None:
        runtime = RetrieverConfig(top_k=0)
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=None), 1)

    def test_wrong_search_config_type_raises(self) -> None:
        runtime = RetrieverConfig()
        with self.assertRaises(TypeError):
            _resolve_request_top_k(runtime_config=runtime, search_config="bad")  # type: ignore[arg-type]


class ValidateSearchRequestConfigTests(unittest.TestCase):
    def _make_config(self, *, prefix_cache: bool = False, method: str = "auto") -> RetrieverConfig:
        progressive = ProgressiveRetrieverConfig(
            prefix_cache_enabled=prefix_cache,
            generation_backend="transformers_prefix_cached" if prefix_cache else "openai",
        )
        return RetrieverConfig(method=method, progressive=progressive)

    def test_same_top_k_ok(self) -> None:
        cfg = self._make_config(prefix_cache=True)
        _validate_search_request_config(runtime_config=cfg, request_top_k=10)  # should not raise

    def test_different_top_k_with_prefix_cache_raises(self) -> None:
        cfg = self._make_config(prefix_cache=True, method="progressive")
        with self.assertRaises(ValueError) as cm:
            _validate_search_request_config(runtime_config=cfg, request_top_k=5)
        self.assertIn("prefix cache", str(cm.exception).lower())

    def test_different_top_k_without_prefix_cache_ok(self) -> None:
        cfg = self._make_config(prefix_cache=False)
        _validate_search_request_config(runtime_config=cfg, request_top_k=5)  # should not raise


class ProgressiveFixedPrefixCacheRequestedTests(unittest.TestCase):
    def test_vllm_with_prefix_cache(self) -> None:
        cfg = MagicMock(generation_backend="vllm", prefix_cache_enabled=True)
        self.assertTrue(_progressive_fixed_prefix_cache_requested(cfg))

    def test_vllm_without_prefix_cache(self) -> None:
        cfg = MagicMock(generation_backend="vllm", prefix_cache_enabled=False)
        self.assertFalse(_progressive_fixed_prefix_cache_requested(cfg))

    def test_openai_backend_false(self) -> None:
        cfg = MagicMock(generation_backend="openai", prefix_cache_enabled=True)
        self.assertFalse(_progressive_fixed_prefix_cache_requested(cfg))

    def test_local_vllm_with_prefix_cache(self) -> None:
        cfg = MagicMock(generation_backend="local_vllm", prefix_cache_enabled=True)
        self.assertTrue(_progressive_fixed_prefix_cache_requested(cfg))

    def test_transformers_prefix_cached_with_prefix_cache(self) -> None:
        cfg = MagicMock(generation_backend="transformers_prefix_cached", prefix_cache_enabled=True)
        self.assertTrue(_progressive_fixed_prefix_cache_requested(cfg))

    def test_transformers_prefix_cached_generation_variant(self) -> None:
        cfg = MagicMock(generation_backend="transformers_prefix_cached_generation", prefix_cache_enabled=True)
        self.assertTrue(_progressive_fixed_prefix_cache_requested(cfg))


class PrefixCachedGenerationRequestedTests(unittest.TestCase):
    def test_correct_backend_and_cache_enabled(self) -> None:
        cfg = MagicMock(generation_backend="transformers_prefix_cached", prefix_cache_enabled=True)
        self.assertTrue(_prefix_cached_generation_requested(cfg))

    def test_cache_disabled(self) -> None:
        cfg = MagicMock(generation_backend="transformers_prefix_cached", prefix_cache_enabled=False)
        self.assertFalse(_prefix_cached_generation_requested(cfg))

    def test_wrong_backend(self) -> None:
        cfg = MagicMock(generation_backend="vllm", prefix_cache_enabled=True)
        self.assertFalse(_prefix_cached_generation_requested(cfg))

    def test_generation_variant(self) -> None:
        cfg = MagicMock(generation_backend="transformers_prefix_cached_generation", prefix_cache_enabled=True)
        self.assertTrue(_prefix_cached_generation_requested(cfg))


class PrefixCachedGenerationConfiguredTests(unittest.TestCase):
    def test_requested_with_model_path(self) -> None:
        cfg = MagicMock(
            generation_backend="transformers_prefix_cached",
            prefix_cache_enabled=True,
            generation_model_path="/models/test",
        )
        self.assertTrue(_prefix_cached_generation_configured(cfg))

    def test_requested_without_model_path(self) -> None:
        cfg = MagicMock(
            generation_backend="transformers_prefix_cached",
            prefix_cache_enabled=True,
            generation_model_path="",
        )
        self.assertFalse(_prefix_cached_generation_configured(cfg))

    def test_not_requested(self) -> None:
        cfg = MagicMock(
            generation_backend="openai",
            prefix_cache_enabled=False,
            generation_model_path="/models/test",
        )
        self.assertFalse(_prefix_cached_generation_configured(cfg))


class ProgressiveModelNameTests(unittest.TestCase):
    def test_generation_model_path_takes_priority(self) -> None:
        cfg = MagicMock(generation_model_path="/gen/model", scoring_backend_model_path="/score/model")
        self.assertEqual(_progressive_model_name("llm_model", cfg), "/gen/model")

    def test_scoring_model_path_fallback(self) -> None:
        cfg = MagicMock(generation_model_path="", scoring_backend_model_path="/score/model")
        self.assertEqual(_progressive_model_name("llm_model", cfg), "/score/model")

    def test_llm_model_final_fallback(self) -> None:
        cfg = MagicMock(generation_model_path="", scoring_backend_model_path="")
        self.assertEqual(_progressive_model_name("my-llm", cfg), "my-llm")

    def test_all_empty(self) -> None:
        cfg = MagicMock(generation_model_path="", scoring_backend_model_path="")
        self.assertEqual(_progressive_model_name("", cfg), "")


class ProgressiveRuntimeLogIdentityTests(unittest.TestCase):
    def test_generation_backend_identity(self) -> None:
        cfg = MagicMock(
            generation_backend="transformers_prefix_cached",
            generation_model_path="/gen",
            generation_tokenizer_path="/tok",
        )
        backend, model, tokenizer = _progressive_runtime_log_identity(cfg)
        self.assertEqual(backend, "transformers_prefix_cached")
        self.assertEqual(model, "/gen")
        self.assertEqual(tokenizer, "/tok")

    def test_generation_backend_tokenizer_defaults_to_model(self) -> None:
        cfg = MagicMock(
            generation_backend="vllm",
            generation_model_path="/gen",
            generation_tokenizer_path="",
        )
        _, _, tokenizer = _progressive_runtime_log_identity(cfg)
        self.assertEqual(tokenizer, "/gen")

    def test_scoring_backend_identity(self) -> None:
        cfg = MagicMock(
            generation_backend="openai",
            scoring_backend="transformers",
            scoring_backend_model_path="/score",
            scoring_backend_tokenizer_path="/score-tok",
        )
        backend, model, tokenizer = _progressive_runtime_log_identity(cfg)
        self.assertEqual(backend, "transformers")
        self.assertEqual(model, "/score")
        self.assertEqual(tokenizer, "/score-tok")


class ProgressiveSearchBackendNameTests(unittest.TestCase):
    def test_generation_backend_returned(self) -> None:
        cfg = MagicMock(generation_backend="vllm", scoring_backend="transformers")
        self.assertEqual(_progressive_search_backend_name(cfg), "vllm")

    def test_fallback_to_scoring_backend(self) -> None:
        cfg = MagicMock(generation_backend="openai", scoring_backend="transformers")
        self.assertEqual(_progressive_search_backend_name(cfg), "transformers")

    def test_both_empty_returns_generate(self) -> None:
        cfg = MagicMock(generation_backend="", scoring_backend="")
        self.assertEqual(_progressive_search_backend_name(cfg), "generate")

    def test_local_vllm(self) -> None:
        cfg = MagicMock(generation_backend="local_vllm", scoring_backend="")
        self.assertEqual(_progressive_search_backend_name(cfg), "local_vllm")


# ── retriever.py (Retriever class) ───────────────────────────────────────


class RetrieverInitTests(unittest.TestCase):
    def test_empty_catalog_builds_empty_maps(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_public_name_by_payload"), {})
        self.assertEqual(_private_attr(r, "_public_name_by_choice_id"), {})

    def test_catalog_record_maps_payload_to_name(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A", description="desc A"),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_public_name_by_payload")["p1"], "Skill A")
        self.assertEqual(_private_attr(r, "_description_by_payload")["p1"], "desc A")

    def test_catalog_record_maps_choice_id_to_name(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A"),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_public_name_by_choice_id")["c1"], "Skill A")

    def test_catalog_record_worker_id_from_field(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="p1", name="S", worker_id="w1"),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_worker_id_by_payload")["p1"], "w1")
        self.assertEqual(_private_attr(r, "_worker_id_by_choice_id")["c1"], "w1")

    def test_catalog_record_worker_id_from_metadata(self) -> None:
        records = [
            CatalogRecord(
                choice_id="c1", payload="p1", name="S",
                worker_id="",
                metadata={"worker_id": "w_meta"},
            ),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_worker_id_by_payload")["p1"], "w_meta")

    def test_catalog_record_worker_id_field_overrides_metadata(self) -> None:
        records = [
            CatalogRecord(
                choice_id="c1", payload="p1", name="S",
                worker_id="w_field",
                metadata={"worker_id": "w_meta"},
            ),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_worker_id_by_payload")["p1"], "w_field")

    def test_catalog_record_name_fallback_to_choice_id(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="p1", name=""),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_public_name_by_payload")["p1"], "c1")

    def test_empty_payload_skipped(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="", name="Skill A"),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertNotIn("", _private_attr(r, "_public_name_by_payload"))

    def test_empty_choice_id_skipped(self) -> None:
        records = [
            CatalogRecord(choice_id="", payload="p1", name="Skill A"),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertNotIn("", _private_attr(r, "_public_name_by_choice_id"))

    def test_description_not_stored_when_empty(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="p1", name="S", description=""),
        ]
        index = _make_loaded_index(catalog_records=records)
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        self.assertNotIn("p1", _private_attr(r, "_description_by_payload"))
        self.assertNotIn("c1", _private_attr(r, "_description_by_choice_id"))

    def test_llm_model_stored(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm_model="gpt-4")
        self.assertEqual(_private_attr(r, "_llm_model"), "gpt-4")

    def test_llm_model_whitespace_stripped(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm_model="  gpt-4  ")
        self.assertEqual(_private_attr(r, "_llm_model"), "gpt-4")

    def test_config_search_config_converted(self) -> None:
        index = _make_loaded_index()
        search_cfg = SearchConfig(top_k=5)
        r = Retriever(loaded_index=index, config=search_cfg)
        self.assertIsInstance(_private_attr(r, "_config"), RetrieverConfig)
        self.assertEqual(_private_attr(r, "_config").top_k, 5)

    def test_config_none_uses_default(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=None)
        self.assertEqual(_private_attr(r, "_config").method, "auto")


class RetrieverProgressiveUnavailableReasonTests(unittest.TestCase):
    def _make_retriever(
        self,
        *,
        llm: MagicMock | None = None,
        llm_model: str = "gpt-4",
        config: RetrieverConfig | None = None,
    ) -> Retriever:
        index = _make_loaded_index()
        if llm is not None:
            llm.capabilities = LLMClientCapabilities(completion=True, streaming=False, candidate_scoring=False)
        return Retriever(
            loaded_index=index,
            config=config or RetrieverConfig(),
            llm=llm,
            llm_model=llm_model,
        )

    def test_llm_available_returns_none(self) -> None:
        r = self._make_retriever(llm=_make_llm_client(completion=True))
        config = RetrieverConfig()
        self.assertIsNone(_private_call(r, "_progressive_unavailable_reason", config))

    def test_no_config_returns_reason(self) -> None:
        r = self._make_retriever(llm=None, llm_model="")
        self.assertEqual(_private_call(r, "_progressive_unavailable_reason", None), "llm client is unavailable")

    def test_no_llm_logit_selection_path(self) -> None:
        progressive = ProgressiveRetrieverConfig(
            single_forward_logit_selection_enabled=True,
            selection_mode="logit_selection",
            compact_boundary_codes_enabled=True,
            scoring_backend_model_path="/models/test",
            scoring_fallback_mode="error",
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        self.assertIsNone(_private_call(r, "_progressive_unavailable_reason", config))

    def test_no_llm_selection_mode_wrong(self) -> None:
        progressive = ProgressiveRetrieverConfig(
            single_forward_logit_selection_enabled=True,
            selection_mode="generate",
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        reason = _private_call(r, "_progressive_unavailable_reason", config)
        self.assertIsNotNone(reason)
        self.assertIn("selection_mode", reason)  # type: ignore[arg-type]

    def test_no_llm_compact_boundary_disabled(self) -> None:
        progressive = ProgressiveRetrieverConfig(
            single_forward_logit_selection_enabled=True,
            selection_mode="logit_selection",
            compact_boundary_codes_enabled=False,
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        reason = _private_call(r, "_progressive_unavailable_reason", config)
        self.assertIsNotNone(reason)
        self.assertIn("compact boundary", reason)  # type: ignore[arg-type]

    def test_prefix_cache_empty_model_path(self) -> None:
        progressive = ProgressiveRetrieverConfig(
            prefix_cache_enabled=True,
            generation_backend="transformers_prefix_cached",
            generation_model_path="",
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        reason = _private_call(r, "_progressive_unavailable_reason", config)
        self.assertIsNotNone(reason)
        self.assertIn("model path", reason)  # type: ignore[arg-type]

    def test_prefix_cache_with_model_path_available(self) -> None:
        progressive = ProgressiveRetrieverConfig(
            prefix_cache_enabled=True,
            generation_backend="transformers_prefix_cached",
            generation_model_path="/models/gen",
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        self.assertIsNone(_private_call(r, "_progressive_unavailable_reason", config))

    def test_scoring_fallback_generate_no_completion(self) -> None:
        """When logit selection is fully enabled but fallback=generate and no completion client."""
        progressive = ProgressiveRetrieverConfig(
            single_forward_logit_selection_enabled=True,
            selection_mode="logit_selection",
            compact_boundary_codes_enabled=True,
            scoring_fallback_mode="generate",
            scoring_backend_model_path="/models/test",
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        reason = _private_call(r, "_progressive_unavailable_reason", config)
        self.assertIsNotNone(reason)
        self.assertIn("generate", reason)  # type: ignore[arg-type]

    def test_no_llm_empty_scoring_model_path(self) -> None:
        progressive = ProgressiveRetrieverConfig(
            single_forward_logit_selection_enabled=True,
            selection_mode="logit_selection",
            compact_boundary_codes_enabled=True,
            scoring_fallback_mode="error",
            scoring_backend_model_path="",
        )
        config = RetrieverConfig(progressive=progressive)
        r = self._make_retriever(llm=None, llm_model="", config=config)
        reason = _private_call(r, "_progressive_unavailable_reason", config)
        self.assertIsNotNone(reason)
        self.assertIn("scoring backend model path", reason)  # type: ignore[arg-type]

    def test_llm_no_model_returns_reason(self) -> None:
        llm = _make_llm_client(completion=True)
        r = self._make_retriever(llm=llm, llm_model="")
        reason = _private_call(r, "_progressive_unavailable_reason")
        self.assertIsNotNone(reason)

    def test_llm_no_completion_returns_reason(self) -> None:
        llm = _make_llm_client(completion=False)
        r = self._make_retriever(llm=llm, llm_model="gpt-4")
        reason = _private_call(r, "_progressive_unavailable_reason")
        self.assertIsNotNone(reason)


class RetrieverBuildScoredCandidateRecordsTests(unittest.TestCase):
    def test_hits_mapped_to_records(self) -> None:
        hits = [
            MagicMock(rank=1, choice_id="c1", payload="p1", score=0.9, description="d1"),
            MagicMock(rank=2, choice_id="c2", payload="p2", score=0.5, description="d2"),
        ]
        records = _private_call(Retriever, "_build_scored_candidate_records", hits=hits, source="progressive")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["rank"], 1)
        self.assertEqual(records[0]["source"], "progressive")
        self.assertAlmostEqual(records[0]["score"], 0.9)
        self.assertTrue(records[0]["selected"])
        self.assertFalse(records[1]["selected"])

    def test_missing_attributes_use_defaults(self) -> None:
        hits = [type("Hit", (), {})()]
        records = _private_call(Retriever, "_build_scored_candidate_records", hits=hits, source="test")
        self.assertEqual(records[0]["rank"], 0)
        self.assertEqual(records[0]["score"], 0.0)
        self.assertFalse(records[0]["selected"])


class RetrieverNormalizeCandidateRecordsTests(unittest.TestCase):
    def test_fills_defaults(self) -> None:
        records = [{"raw_output": "x", "resolved_payload": "p"}]
        normalized = _private_call(Retriever, "_normalize_candidate_records", records, source="test")
        self.assertEqual(normalized[0]["rank"], 1)
        self.assertTrue(normalized[0]["valid"])
        self.assertFalse(normalized[0]["selected"])
        self.assertEqual(normalized[0]["source"], "test")

    def test_missing_rank_uses_index(self) -> None:
        records = [
            {"raw_output": "a", "resolved_payload": "p1"},
            {"raw_output": "b", "resolved_payload": "p2", "rank": 5},
        ]
        normalized = _private_call(Retriever, "_normalize_candidate_records", records, source="test")
        self.assertEqual(normalized[0]["rank"], 1)
        self.assertEqual(normalized[1]["rank"], 5)

    def test_source_parameter(self) -> None:
        records = [{"raw_output": "x"}]
        normalized = _private_call(Retriever, "_normalize_candidate_records", records, source="my_source")
        self.assertEqual(normalized[0]["source"], "my_source")

    def test_preserves_existing_score(self) -> None:
        records = [{"raw_output": "x", "score": 0.9}]
        normalized = _private_call(Retriever, "_normalize_candidate_records", records, source="test")
        self.assertAlmostEqual(normalized[0]["score"], 0.9)


class RetrieverPublicNameForTests(unittest.TestCase):
    def _make_retriever_with_records(self, *records: CatalogRecord) -> Retriever:
        index = _make_loaded_index(catalog_records=records)
        return Retriever(loaded_index=index, config=RetrieverConfig())

    def test_payload_lookup(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A"),
        )
        self.assertEqual(_private_call(r, "_public_name_for", payload="p1"), "Skill A")

    def test_choice_id_lookup(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A"),
        )
        self.assertEqual(_private_call(r, "_public_name_for", choice_id="c1"), "Skill A")

    def test_fallback_to_payload_lookup(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A"),
        )
        self.assertEqual(_private_call(r, "_public_name_for", fallback="p1"), "Skill A")

    def test_fallback_to_choice_id_lookup(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A"),
        )
        self.assertEqual(_private_call(r, "_public_name_for", fallback="c1"), "Skill A")

    def test_unknown_returns_fallback(self) -> None:
        r = self._make_retriever_with_records()
        self.assertEqual(_private_call(r, "_public_name_for", payload="unknown"), "unknown")

    def test_empty_strings_return_empty(self) -> None:
        r = self._make_retriever_with_records()
        self.assertEqual(_private_call(r, "_public_name_for"), "")


class RetrieverWorkerIdForTests(unittest.TestCase):
    def _make_retriever_with_records(self, *records: CatalogRecord) -> Retriever:
        index = _make_loaded_index(catalog_records=records)
        return Retriever(loaded_index=index, config=RetrieverConfig())

    def test_payload_lookup(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="S", worker_id="w1"),
        )
        self.assertEqual(_private_call(r, "_worker_id_for", payload="p1"), "w1")

    def test_choice_id_lookup(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="S", worker_id="w1"),
        )
        self.assertEqual(_private_call(r, "_worker_id_for", choice_id="c1"), "w1")

    def test_fallback_chain(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="S", worker_id="w1"),
        )
        self.assertEqual(_private_call(r, "_worker_id_for", fallback="p1"), "w1")

    def test_unknown_returns_input(self) -> None:
        r = self._make_retriever_with_records()
        self.assertEqual(_private_call(r, "_worker_id_for", payload="unknown"), "unknown")


class RetrieverPublicizeCandidateRecordTests(unittest.TestCase):
    def _make_retriever_with_records(self, *records: CatalogRecord) -> Retriever:
        index = _make_loaded_index(catalog_records=records)
        return Retriever(loaded_index=index, config=RetrieverConfig())

    def test_sets_resolved_cid_and_public_payload(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="Skill A", worker_id="w1"),
        )
        record = {"resolved_payload": "p1", "choice_id": "c1", "raw_output": "c1"}
        result = _private_call(r, "_publicize_candidate_record", record)
        self.assertEqual(result["resolved_cid"], "p1")
        self.assertEqual(result["resolved_payload"], "w1")
        self.assertEqual(result["skill_name"], "Skill A")

    def test_sets_worker_id(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="S", worker_id="w1"),
        )
        record = {"resolved_payload": "p1", "choice_id": "c1", "raw_output": ""}
        result = _private_call(r, "_publicize_candidate_record", record)
        self.assertEqual(result["worker_id"], "w1")

    def test_description_from_catalog(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="S", description="my desc"),
        )
        record = {"resolved_payload": "p1", "choice_id": "c1", "raw_output": ""}
        result = _private_call(r, "_publicize_candidate_record", record)
        self.assertEqual(result["description"], "my desc")

    def test_description_fallback_to_record(self) -> None:
        r = self._make_retriever_with_records(
            CatalogRecord(choice_id="c1", payload="p1", name="S", description=""),
        )
        record = {"resolved_payload": "p1", "choice_id": "c1", "raw_output": "", "description": "inline desc"}
        result = _private_call(r, "_publicize_candidate_record", record)
        self.assertEqual(result["description"], "inline desc")

    def test_empty_payload_no_resolved_cid(self) -> None:
        r = self._make_retriever_with_records()
        record = {"resolved_payload": "", "choice_id": "", "raw_output": ""}
        result = _private_call(r, "_publicize_candidate_record", record)
        self.assertNotIn("resolved_cid", result)


class RetrieverDedupePublicCandidateRecordsTests(unittest.TestCase):
    def test_dedupes_by_resolved_payload(self) -> None:
        records = [
            {"resolved_payload": "a", "worker_id": "w1", "skill_name": "S1", "raw_output": "a"},
            {"resolved_payload": "a", "worker_id": "w1", "skill_name": "S1", "raw_output": "a"},
            {"resolved_payload": "b", "worker_id": "w2", "skill_name": "S2", "raw_output": "b"},
        ]
        deduped = _private_call(Retriever, "_dedupe_public_candidate_records", records)
        self.assertEqual(len(deduped), 2)

    def test_reranks_after_dedup(self) -> None:
        records = [
            {"resolved_payload": "a", "worker_id": "w1", "skill_name": "S1", "raw_output": "a"},
            {"resolved_payload": "b", "worker_id": "w2", "skill_name": "S2", "raw_output": "b"},
        ]
        deduped = _private_call(Retriever, "_dedupe_public_candidate_records", records)
        self.assertEqual(deduped[0]["rank"], 1)
        self.assertTrue(deduped[0]["selected"])
        self.assertEqual(deduped[1]["rank"], 2)
        self.assertFalse(deduped[1]["selected"])

    def test_empty_key_skipped(self) -> None:
        records = [
            {"resolved_payload": "", "worker_id": "", "skill_name": "", "raw_output": ""},
            {"resolved_payload": "a", "worker_id": "w1", "skill_name": "S1", "raw_output": "a"},
        ]
        deduped = _private_call(Retriever, "_dedupe_public_candidate_records", records)
        self.assertEqual(len(deduped), 1)

    def test_empty_input(self) -> None:
        deduped = _private_call(Retriever, "_dedupe_public_candidate_records", [])
        self.assertEqual(deduped, [])


class RetrieverTrimPublicSearchResultTests(unittest.TestCase):
    def test_trims_to_top_k(self) -> None:
        result = _sample_result(n=5)
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=2)
        self.assertEqual(len(trimmed.candidate_records), 2)
        self.assertEqual(len(trimmed.payloads), 2)

    def test_reranks_after_trim(self) -> None:
        result = _sample_result(n=5)
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=2)
        self.assertEqual(trimmed.candidate_records[0]["rank"], 1)
        self.assertTrue(trimmed.candidate_records[0]["selected"])
        self.assertEqual(trimmed.candidate_records[1]["rank"], 2)

    def test_top_k_larger_than_records(self) -> None:
        result = _sample_result(n=3)
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=10)
        self.assertEqual(len(trimmed.candidate_records), 3)

    def test_top_k_0_clamped_to_1(self) -> None:
        result = _sample_result(n=3)
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=0)
        self.assertEqual(len(trimmed.candidate_records), 1)

    def test_selected_payload_is_first(self) -> None:
        result = _sample_result(n=5)
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=2)
        self.assertEqual(trimmed.selected_payload, "payload_1")
        self.assertEqual(trimmed.selected_rank, 1)

    def test_empty_payloads_filtered(self) -> None:
        result = RetrieverSearchResult(
            method="progressive",
            payloads=["", "a", ""],
            candidate_records=[
                {"resolved_payload": "", "rank": 1},
                {"resolved_payload": "a", "rank": 2},
                {"resolved_payload": "", "rank": 3},
            ],
            summary_lines=[],
            selected_payload="a",
            selected_rank=2,
        )
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=10)
        self.assertNotIn("", trimmed.payloads)

    def test_trace_events_preserved(self) -> None:
        result = _sample_result(n=3)
        result.trace_events = [{"e": 1}]
        trimmed = _private_call(Retriever, "_trim_public_search_result", result, top_k=2)
        self.assertEqual(len(trimmed.trace_events), 1)


class RetrieverBuildPublicSummaryLinesTests(unittest.TestCase):
    def test_with_score(self) -> None:
        records = [{"resolved_payload": "p1", "source": "test", "score": 0.95}]
        lines = _private_call(Retriever, "_build_public_summary_lines", records)
        self.assertEqual(len(lines), 1)
        self.assertIn("0.9500", lines[0])

    def test_without_score(self) -> None:
        records = [{"resolved_payload": "p1", "source": "test", "score": None}]
        lines = _private_call(Retriever, "_build_public_summary_lines", records)
        self.assertEqual(len(lines), 1)
        self.assertNotIn("score=", lines[0])

    def test_invalid_score(self) -> None:
        records = [{"resolved_payload": "p1", "source": "test", "score": "not_a_number"}]
        lines = _private_call(Retriever, "_build_public_summary_lines", records)
        self.assertEqual(len(lines), 1)
        self.assertIn("score=not_a_number", lines[0])

    def test_fallback_to_raw_output(self) -> None:
        records = [{"raw_output": "r1", "source": "test", "score": None}]
        lines = _private_call(Retriever, "_build_public_summary_lines", records)
        self.assertIn("r1", lines[0])

    def test_empty_label_uses_dash(self) -> None:
        records = [{"source": "test", "score": None}]
        lines = _private_call(Retriever, "_build_public_summary_lines", records)
        self.assertIn("-", lines[0])

    def test_empty_source_defaults_to_unknown(self) -> None:
        records = [{"resolved_payload": "p1", "score": None}]
        lines = _private_call(Retriever, "_build_public_summary_lines", records)
        self.assertIn("source=unknown", lines[0])


class RetrieverSearchTests(unittest.TestCase):
    def test_delegates_to_search_details(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        with patch.object(r, "search_details") as mock_sd:
            mock_sd.return_value = _sample_result(n=2)
            payloads = r.search("test query")
            mock_sd.assert_called_once_with("test query", search_config=None)
            self.assertEqual(payloads, ["payload_1", "payload_2"])

    def test_passes_search_config(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        cfg = SearchRequestConfig(top_k=3)
        with patch.object(r, "search_details") as mock_sd:
            mock_sd.return_value = _sample_result(n=1)
            r.search("q", search_config=cfg)
            mock_sd.assert_called_once_with("q", search_config=cfg)


class RetrieverCloseTests(unittest.TestCase):
    def test_closes_llm_client(self) -> None:
        llm = _make_llm_client()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm)
        r.close()
        llm.close.assert_called_once()

    def test_skips_non_callable_close(self) -> None:
        llm = MagicMock()
        llm.close = "not_callable"
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm)
        r.close()  # should not raise

    def test_handles_close_exception(self) -> None:
        llm = _make_llm_client()
        llm.close.side_effect = RuntimeError("boom")
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm)
        r.close()  # should not raise

    def test_closes_cached_clients(self) -> None:
        llm = _make_llm_client()
        cached = MagicMock()
        cached.close = MagicMock()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm)
        _private_attr(r, "_progressive_runtime_cache")[("key",)] = cached
        r.close()
        cached.close.assert_called_once()

    def test_no_duplicate_close_calls(self) -> None:
        llm = _make_llm_client()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm)
        # Simulate same client in cache
        _private_attr(r, "_progressive_runtime_cache")[("key",)] = llm
        r.close()
        self.assertEqual(llm.close.call_count, 1)

    def test_clears_caches(self) -> None:
        llm = _make_llm_client()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm)
        _private_attr(r, "_progressive_runtime_cache")["key"] = llm
        _private_attr(r, "_progressive_retriever_cache")["key"] = MagicMock()
        r.close()
        self.assertEqual(len(_private_attr(r, "_progressive_runtime_cache")), 0)
        self.assertEqual(len(_private_attr(r, "_progressive_retriever_cache")), 0)


class RetrieverSearchDetailsTests(unittest.TestCase):
    def test_full_pipeline_with_mocked_method(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(top_k=3))

        with patch("retrieval.service.retriever.create_retrieval_method") as mock_create:
            method = MagicMock()
            method.search.return_value = _sample_result(n=3)
            mock_create.return_value = method

            result = r.search_details("test query")
            self.assertIsInstance(result, RetrieverSearchResult)
            method.search.assert_called_once()

    def test_request_top_k_override(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(top_k=10))

        with patch("retrieval.service.retriever.create_retrieval_method") as mock_create:
            method = MagicMock()
            method.search.return_value = _sample_result(n=2)
            mock_create.return_value = method

            search_cfg = SearchRequestConfig(top_k=2)
            result = r.search_details("q", search_config=search_cfg)
            request = method.search.call_args[0][0]
            self.assertEqual(request.top_k, 2)


class RetrieverFromIndexTests(unittest.TestCase):
    def test_from_index_loads_and_constructs(self) -> None:
        fake_index = _make_loaded_index()
        with patch("retrieval.service.retriever.load_retriever_index", return_value=fake_index):
            r = Retriever.from_index("/tmp/fake_index")
            self.assertIsInstance(r, Retriever)

    def test_from_index_passes_llm_client(self) -> None:
        fake_index = _make_loaded_index()
        llm = _make_llm_client()
        with patch("retrieval.service.retriever.load_retriever_index", return_value=fake_index):
            with patch("retrieval.service.retriever._coerce_llm_client", return_value=llm) as mock_coerce:
                r = Retriever.from_index("/tmp/fake_index", llm_openai_client=llm)
                mock_coerce.assert_called_once_with(llm)

    def test_from_index_passes_config(self) -> None:
        fake_index = _make_loaded_index()
        search_cfg = SearchConfig(top_k=5)
        with patch("retrieval.service.retriever.load_retriever_index", return_value=fake_index):
            r = Retriever.from_index("/tmp/fake_index", config=search_cfg)
            self.assertIsInstance(_private_attr(r, "_config"), RetrieverConfig)
            self.assertEqual(_private_attr(r, "_config").top_k, 5)


class RetrieverDebugEventHookTests(unittest.TestCase):
    def test_hook_called(self) -> None:
        hook = MagicMock()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), debug_event_hook=hook)
        _private_call(r, "_record_debug_event", {"type": "test"})
        hook.assert_called_once_with({"type": "test"})

    def test_hook_exception_swallowed(self) -> None:
        hook = MagicMock(side_effect=ValueError("boom"))
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), debug_event_hook=hook)
        _private_call(r, "_record_debug_event", {"type": "test"})  # should not raise

    def test_no_hook_no_error(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig())
        _private_call(r, "_record_debug_event", {"type": "test"})  # should not raise


class RetrieverEmitRuntimeEventTests(unittest.TestCase):
    def test_emits_progressive_runtime_event(self) -> None:
        hook = MagicMock()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), debug_event_hook=hook)
        _private_call(r, "_emit_runtime_event", phase="test_phase")
        hook.assert_called_once()
        call_args = hook.call_args[0][0]
        self.assertEqual(call_args["type"], "progressive_runtime")
        self.assertEqual(call_args["phase"], "test_phase")


class RetrieverEmitFallbackEventTests(unittest.TestCase):
    def test_emits_fallback_event(self) -> None:
        hook = MagicMock()
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), debug_event_hook=hook)
        _private_call(
            r,
            "_emit_fallback_event",
            requested_method="progressive",
            fallback_method="generate",
            reason="no llm",
        )
        hook.assert_called_once()
        call_args = hook.call_args[0][0]
        self.assertEqual(call_args["type"], "retriever_fallback")
        self.assertEqual(call_args["requested_method"], "progressive")


class RetrieverCanRunProgressiveTests(unittest.TestCase):
    def test_available(self) -> None:
        index = _make_loaded_index()
        llm = _make_llm_client(completion=True)
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=llm, llm_model="gpt-4")
        self.assertTrue(_private_call(r, "_can_run_progressive", RetrieverConfig()))

    def test_unavailable(self) -> None:
        index = _make_loaded_index()
        r = Retriever(loaded_index=index, config=RetrieverConfig(), llm=None, llm_model="")
        self.assertFalse(_private_call(r, "_can_run_progressive", RetrieverConfig()))


if __name__ == "__main__":
    unittest.main()
