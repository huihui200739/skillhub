from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from indexing.embedding.index import OpenAIEmbeddingClient
from orchestration.llm.client import LLMClient
from retrieval.lexical.bm25 import BM25Finder
from retrieval.semantic.embedding import EmbeddingFinder
from retrieval.tree.progressive import ProgressiveFinder

from ..io.loading import LoadedFinderIndex, load_finder_index
from ..merge.append import hits_to_search_result, merge_search_results
from .defaults import normalize_method, serialize_hit_summary, serialize_trace_event
from .models import (
    RetrievalMethod,
    RetrieverConfig,
    RetrieverSearchResult,
    SearchConfig,
    runtime_retriever_config_from_search,
)


class Retriever:
    """Unified online retrieval API."""

    def __init__(
        self,
        *,
        loaded_index: LoadedFinderIndex,
        config: RetrieverConfig | None = None,
        llm: Any | None = None,
        llm_model: str = "",
        embedding_client: Any | None = None,
        debug_event_hook: Callable[[Dict[str, object]], None] | None = None,
        prefix_audit_hook: Callable[[str, str, List[Dict[str, str]]], None] | None = None,
        before_llm_call_hook: Callable[[], None] | None = None,
    ) -> None:
        self._loaded_index = loaded_index
        self._config = config or RetrieverConfig()
        self._llm = llm
        self._llm_model = str(llm_model or "").strip()
        self._embedding_client = embedding_client
        self._debug_event_hook = debug_event_hook
        self._prefix_audit_hook = prefix_audit_hook
        self._before_llm_call_hook = before_llm_call_hook

    @classmethod
    def from_index(
        cls,
        index_dir: str | Path,
        *,
        llm_openai_client: Any | None = None,
        llm_model: str = "",
        embedding_openai_client: Any | None = None,
        embedding_model: str = "",
        debug_event_hook: Callable[[Dict[str, object]], None] | None = None,
        prefix_audit_hook: Callable[[str, str, List[Dict[str, str]]], None] | None = None,
        before_llm_call_hook: Callable[[], None] | None = None,
    ) -> "Retriever":
        loaded_index = load_finder_index(index_dir)
        return cls(
            loaded_index=loaded_index,
            config=RetrieverConfig(),
            llm=_coerce_llm_client(llm_openai_client),
            llm_model=str(llm_model or "").strip(),
            embedding_client=_coerce_embedding_client(embedding_openai_client, embedding_model),
            debug_event_hook=debug_event_hook,
            prefix_audit_hook=prefix_audit_hook,
            before_llm_call_hook=before_llm_call_hook,
        )

    def search(self, query: str, *, config: SearchConfig) -> List[str]:
        return list(self.search_details(query, config=config).payloads)

    def search_details(
        self,
        query: str | Sequence[Dict[str, str]],
        *,
        config: SearchConfig,
    ) -> RetrieverSearchResult:
        runtime_config = runtime_retriever_config_from_search(config)
        resolved_top_k = max(1, int(runtime_config.top_k))
        requested_method = normalize_method(runtime_config.method)
        if requested_method == RetrievalMethod.BM25.value:
            return self._search_bm25(query=query, top_k=resolved_top_k, runtime_config=runtime_config)
        if requested_method == RetrievalMethod.EMBEDDING.value:
            return self._search_embedding_then_bm25(query=query, top_k=resolved_top_k, runtime_config=runtime_config)
        if requested_method == RetrievalMethod.PROGRESSIVE.value:
            return self._search_progressive_with_optional_embedding_fill(
                query=query,
                top_k=resolved_top_k,
                runtime_config=runtime_config,
                llm_top_k=config.llm_top_k,
            )
        return self._search_unified(
            query=query,
            top_k=resolved_top_k,
            runtime_config=runtime_config,
            llm_top_k=config.llm_top_k,
        )

    def _search_unified(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        runtime_config: RetrieverConfig,
        llm_top_k: int | None,
    ) -> RetrieverSearchResult:
        if self._can_run_progressive():
            primary_result = self._search_progressive(query=query, top_k=top_k, runtime_config=runtime_config)
            primary_result = _truncate_primary_result(primary_result, top_k=top_k, llm_top_k=llm_top_k)
            secondary_results: List[RetrieverSearchResult] = []
            if self._can_run_embedding():
                secondary_results.append(
                    self._search_embedding(
                        query=query,
                        top_k=top_k,
                        runtime_config=runtime_config))
            secondary_results.append(self._search_bm25(query=query, top_k=top_k, runtime_config=runtime_config))
            return merge_search_results(
                method="progressive+embedding+bm25" if self._can_run_embedding() else "progressive+bm25",
                top_k=top_k,
                primary_result=primary_result,
                secondary_results=secondary_results,
                hybrid_config=runtime_config.hybrid,
            )
        if self._can_run_embedding():
            return self._search_embedding_then_bm25(query=query, top_k=top_k, runtime_config=runtime_config)
        return self._search_bm25(query=query, top_k=top_k, runtime_config=runtime_config)

    def _search_embedding_then_bm25(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        runtime_config: RetrieverConfig,
    ) -> RetrieverSearchResult:
        if not self._can_run_embedding():
            return self._search_bm25(query=query, top_k=top_k, runtime_config=runtime_config)
        return merge_search_results(
            method="embedding+bm25",
            top_k=top_k,
            primary_result=self._search_embedding(query=query, top_k=top_k, runtime_config=runtime_config),
            secondary_results=[self._search_bm25(query=query, top_k=top_k, runtime_config=runtime_config)],
            hybrid_config=runtime_config.hybrid,
        )

    def _search_progressive_with_optional_embedding_fill(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        runtime_config: RetrieverConfig,
        llm_top_k: int | None,
    ) -> RetrieverSearchResult:
        if not self._can_run_progressive():
            return self._search_embedding_then_bm25(query=query, top_k=top_k, runtime_config=runtime_config)
        primary_result = self._search_progressive(query=query, top_k=top_k, runtime_config=runtime_config)
        if llm_top_k is None or not self._can_run_embedding():
            return primary_result
        head_limit = max(0, min(int(llm_top_k), int(top_k)))
        if head_limit >= top_k:
            return primary_result
        return merge_search_results(
            method="progressive+embedding",
            top_k=top_k,
            primary_result=_truncate_primary_result(primary_result, top_k=top_k, llm_top_k=llm_top_k),
            secondary_results=[self._search_embedding(query=query, top_k=top_k, runtime_config=runtime_config)],
            hybrid_config=runtime_config.hybrid,
        )

    def _can_run_progressive(self) -> bool:
        return self._llm is not None and bool(self._llm_model)

    def _can_run_embedding(self) -> bool:
        return self._embedding_client is not None and self._loaded_index.embedding_index is not None

    def _search_bm25(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        runtime_config: RetrieverConfig,
    ) -> RetrieverSearchResult:
        finder = (
            BM25Finder.from_index(index=self._loaded_index.bm25_index, config=runtime_config.bm25)
            if self._loaded_index.bm25_index is not None
            else BM25Finder.from_choices(choices=self._loaded_index.choices, config=runtime_config.bm25)
        )
        result = finder.retrieve_top_k(query=query, top_k=top_k)
        candidate_records = [
            {
                "rank": hit.rank,
                "raw_output": hit.choice_id,
                "resolved_payload": hit.payload,
                "valid": True,
                "selected": hit.rank == 1,
                "choice_id": hit.choice_id,
                "score": float(hit.score),
                "source": "bm25",
            }
            for hit in result.hits
        ]
        return hits_to_search_result(
            method="bm25",
            source="bm25",
            elapsed_ms=float(result.elapsed_ms),
            candidate_records=candidate_records,
            trace_events=[
                {
                    "event_type": "bm25_retrieval",
                    "node_id": "ROOT",
                    "depth": 0,
                    "detail": {
                        "query_text": result.query_text,
                        "top_k": top_k,
                        "hit_count": len(result.hits),
                        "hits": [
                            serialize_hit_summary(hit.choice_id, hit.payload, hit.rank, hit.score)
                            for hit in result.hits
                        ],
                        "truncation": dict(result.truncation or {}),
                    },
                }
            ],
        )

    def _search_embedding(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        runtime_config: RetrieverConfig,
    ) -> RetrieverSearchResult:
        if not self._can_run_embedding():
            return self._search_bm25(query=query, top_k=top_k, runtime_config=runtime_config)
        finder = EmbeddingFinder(
            embedding_client=self._embedding_client,
            index=self._loaded_index.embedding_index,
            config=runtime_config.embedding,
        )
        result = finder.retrieve_top_k(query=query, top_k=top_k)
        candidate_records = [
            {
                "rank": hit.rank,
                "raw_output": hit.choice_id,
                "resolved_payload": hit.payload,
                "valid": True,
                "selected": hit.rank == 1,
                "choice_id": hit.choice_id,
                "score": float(hit.score),
                "source": "embedding",
            }
            for hit in result.hits
        ]
        return hits_to_search_result(
            method="embedding",
            source="embedding",
            elapsed_ms=float(result.elapsed_ms),
            candidate_records=candidate_records,
            trace_events=[
                {
                    "event_type": "embedding_retrieval",
                    "node_id": "ROOT",
                    "depth": 0,
                    "detail": {
                        "query_text": result.query_text,
                        "top_k": top_k,
                        "hit_count": len(result.hits),
                        "hits": [
                            serialize_hit_summary(hit.choice_id, hit.payload, hit.rank, hit.score)
                            for hit in result.hits
                        ],
                        "truncation": dict(result.truncation or {}),
                    },
                }
            ],
        )

    def _search_progressive(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        runtime_config: RetrieverConfig,
    ) -> RetrieverSearchResult:
        if not self._can_run_progressive():
            return self._search_embedding_then_bm25(query=query, top_k=top_k, runtime_config=runtime_config)
        finder = ProgressiveFinder(
            llm=self._llm,
            config=runtime_config.progressive,
            debug_event_hook=self._debug_event_hook)
        result = finder.search(model=self._llm_model, query=query, root=self._loaded_index.tree_root, top_k=top_k)
        candidate_records = []
        for item in result.candidate_records:
            record = dict(item)
            record.setdefault("source", "progressive")
            candidate_records.append(record)
        return RetrieverSearchResult(
            method="progressive",
            payloads=[candidate.payload for candidate in result.candidates],
            candidate_records=candidate_records,
            summary_lines=list(result.summary_lines),
            selected_payload=result.selected_payload,
            selected_rank=result.selected_rank,
            elapsed_ms=float(result.elapsed_ms),
            trace_events=[serialize_trace_event(event) for event in result.trace.events],
        )


def _coerce_llm_client(client: Any | None) -> Any | None:
    if client is None or hasattr(client, "complete"):
        return client
    return LLMClient(client)


def _coerce_embedding_client(client: Any | None, model: str) -> Any | None:
    if client is None or hasattr(client, "embed_texts"):
        return client
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("embedding_model is required when embedding_openai_client is provided")
    return OpenAIEmbeddingClient(client=client, model=model_name)


def _truncate_primary_result(result: RetrieverSearchResult, *, top_k: int,
                             llm_top_k: int | None) -> RetrieverSearchResult:
    if llm_top_k is None:
        limit = top_k
    else:
        limit = max(0, min(int(llm_top_k), int(top_k)))
    if limit >= len(result.candidate_records):
        return result
    candidate_records = [dict(record) for record in result.candidate_records[:limit]]
    for index, record in enumerate(candidate_records, start=1):
        record["rank"] = index
        record["selected"] = index == 1
    payloads = [str(record.get("resolved_payload") or "") for record in candidate_records]
    return RetrieverSearchResult(
        method=result.method,
        payloads=payloads,
        candidate_records=candidate_records,
        summary_lines=list(result.summary_lines[:limit]),
        selected_payload=payloads[0] if payloads else None,
        selected_rank=1 if payloads else -1,
        elapsed_ms=result.elapsed_ms,
        trace_events=list(result.trace_events),
    )


__all__ = ["Retriever"]
