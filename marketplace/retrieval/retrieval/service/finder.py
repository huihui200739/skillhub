from __future__ import annotations

from typing import Any, Callable, Dict, MutableMapping, Sequence

from indexing import EmbeddingIndex, EmbeddingRecord, build_embedding_index
from retrieval.lexical.bm25 import BM25Finder, BM25FinderConfig, build_bm25_document_text
from retrieval.semantic.embedding import EmbeddingClient, EmbeddingFinder
from retrieval.tree.progressive import CompletionClient, ProgressiveFinder, ProgressiveFinderResult
from retrieval.tree.roots import build_progressive_root, choices_cache_key

from .config import FinderConfig

from ..merge.append import adapt_bm25_result, adapt_embedding_result, merge_progressive_with_backfill


class Finder:
    def __init__(
        self,
        *,
        config: FinderConfig | None = None,
        llm: CompletionClient | None = None,
        embedding_client: EmbeddingClient | None = None,
        embedding_index: EmbeddingIndex | None = None,
        debug_event_hook: Any | None = None,
        prefix_audit_hook: Callable[[str, str, list[dict[str, str]]], None] | None = None,
        before_llm_call_hook: Callable[[], None] | None = None,
    ) -> None:
        self._config = config or FinderConfig()
        self._llm = llm
        self._embedding_client = embedding_client
        self._embedding_index = embedding_index
        self._debug_event_hook = debug_event_hook
        self._prefix_audit_hook = prefix_audit_hook
        self._before_llm_call_hook = before_llm_call_hook
        self._embedding_index_cache: MutableMapping[str, EmbeddingIndex] = {}
        self._progressive_root_cache: MutableMapping[str, object] = {}

    def retrieve_top_k(
        self,
        *,
        model: str,
        query: str | Sequence[Dict[str, str]],
        choices: Sequence[object],
        resolve_candidate: Callable[[str, Dict[str, str]], str],
        system_prompt: str,
        top_k: int | None = None,
        method: str | None = None,
    ) -> ProgressiveFinderResult:
        resolved_top_k = max(1, int(top_k if top_k is not None else self._config.top_k))
        resolved_method = self._resolve_method(method=method, model=model)

        if resolved_method == "bm25":
            return self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)

        if resolved_method == "embedding":
            if self._embedding_client is None:
                return self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)
            embedding_index = self._ensure_embedding_index(choices)
            if embedding_index is None:
                return self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)
            embedding_result = self._search_embedding(
                query=query, top_k=resolved_top_k, embedding_index=embedding_index)
            bm25_result = self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)
            return merge_progressive_with_backfill(
                progressive_result=embedding_result,
                embedding_result=None,
                bm25_result=bm25_result,
                top_k=resolved_top_k,
                hybrid_config=self._config.hybrid,
            )

        if not model or self._llm is None:
            if self._embedding_client is not None:
                embedding_index = self._ensure_embedding_index(choices)
                if embedding_index is not None:
                    embedding_result = self._search_embedding(
                        query=query, top_k=resolved_top_k, embedding_index=embedding_index)
                    bm25_result = self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)
                    return merge_progressive_with_backfill(
                        progressive_result=embedding_result,
                        embedding_result=None,
                        bm25_result=bm25_result,
                        top_k=resolved_top_k,
                        hybrid_config=self._config.hybrid,
                    )
            return self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)

        progressive_result = self._search_progressive(
            model=model,
            query=query,
            choices=choices,
            resolve_candidate=resolve_candidate,
            system_prompt=system_prompt,
            top_k=resolved_top_k,
        )
        embedding_result = None
        if self._embedding_client is not None:
            embedding_index = self._ensure_embedding_index(choices)
            if embedding_index is not None:
                embedding_result = self._search_embedding(
                    query=query, top_k=resolved_top_k, embedding_index=embedding_index)
        bm25_result = self._search_bm25(query=query, choices=choices, top_k=resolved_top_k)
        return merge_progressive_with_backfill(
            progressive_result=progressive_result,
            embedding_result=embedding_result,
            bm25_result=bm25_result,
            top_k=resolved_top_k,
            hybrid_config=self._config.hybrid,
        )

    def _search_progressive(
        self,
        *,
        model: str,
        query: str | Sequence[Dict[str, str]],
        choices: Sequence[object],
        resolve_candidate: Callable[[str, Dict[str, str]], str],
        system_prompt: str,
        top_k: int,
    ) -> ProgressiveFinderResult:
        progressive = ProgressiveFinder(
            llm=self._llm,
            config=self._config.progressive,
            debug_event_hook=self._debug_event_hook)
        progressive_root = build_progressive_root(choices, cache=self._progressive_root_cache)
        if progressive_root is not None and (progressive_root.children or progressive_root.items):
            result = progressive.search(model=model, query=query, root=progressive_root, top_k=top_k)
        else:
            result = progressive.retrieve_top_k(
                model=model,
                query=query,
                choices=choices,
                resolve_candidate=resolve_candidate,
                system_prompt=system_prompt,
                top_k=top_k,
                prefix_audit_hook=self._prefix_audit_hook,
                before_llm_call_hook=self._before_llm_call_hook,
            )
        for record in result.candidate_records:
            record.setdefault("source", "progressive")
        return result

    def _search_bm25(self, *, query: str | Sequence[Dict[str, str]],
                     choices: Sequence[object], top_k: int) -> ProgressiveFinderResult:
        result = BM25Finder.from_choices(
            choices=choices,
            config=BM25FinderConfig(
                top_k=top_k,
                k1=self._config.bm25.k1,
                b=self._config.bm25.b,
                delta=self._config.bm25.delta,
                min_score=self._config.bm25.min_score,
                relative_min_score=self._config.bm25.relative_min_score,
                autocut_jumps=self._config.bm25.autocut_jumps,
                autocut_min_relative_drop=self._config.bm25.autocut_min_relative_drop,
                min_query_term_matches=self._config.bm25.min_query_term_matches,
                min_query_term_match_ratio=self._config.bm25.min_query_term_match_ratio,
            ),
        ).retrieve_top_k(query=query, top_k=top_k)
        return adapt_bm25_result(method="bm25", result=result)

    def _search_embedding(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int,
        embedding_index: EmbeddingIndex,
    ) -> ProgressiveFinderResult:
        result = EmbeddingFinder(
            embedding_client=self._embedding_client,
            index=embedding_index,
            config=self._config.embedding).retrieve_top_k(
            query=query,
            top_k=top_k)
        return adapt_embedding_result(method="embedding", result=result)

    def _resolve_method(self, *, method: str | None, model: str) -> str:
        requested = str(method or self._config.method or "auto").strip().lower()
        if requested in {"", "auto", "progressive"}:
            if model and self._llm is not None:
                return "progressive"
            if self._embedding_client is not None:
                return "embedding"
            return "bm25"
        if requested in {"bm25", "embedding"}:
            return requested
        return "progressive" if model and self._llm is not None else "bm25"

    def _ensure_embedding_index(self, choices: Sequence[object]) -> EmbeddingIndex | None:
        if self._embedding_index is not None:
            return self._embedding_index
        if self._embedding_client is None:
            return None
        cache_key = choices_cache_key(choices)
        cached = self._embedding_index_cache.get(cache_key)
        if cached is not None:
            return cached
        records = [
            EmbeddingRecord(
                choice_id=str(getattr(choice, "choice_id", "") or ""),
                payload=str(getattr(choice, "payload", "") or ""),
                text=build_bm25_document_text(
                    choice_id=str(getattr(choice, "choice_id", "") or ""),
                    payload=str(getattr(choice, "payload", "") or ""),
                    description=str(getattr(choice, "description", "") or ""),
                ),
                description=str(getattr(choice, "description", "") or ""),
            )
            for choice in choices
            if str(getattr(choice, "choice_id", "") or "").strip() and str(getattr(choice, "payload", "") or "").strip()
        ]
        if not records:
            return None
        model_name = str(getattr(self._embedding_client, "model", "") or "embedding-model")
        index = build_embedding_index(
            records=records,
            embedding_client=self._embedding_client,
            model_name=model_name,
            batch_size=max(1, int(self._config.embedding.batch_size)),
        )
        self._embedding_index_cache[cache_key] = index
        return index


__all__ = ["Finder"]
