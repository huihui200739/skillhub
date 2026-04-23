from __future__ import annotations

from models.retrieval import FinderCandidate, FinderTrace
from retrieval.lexical.bm25 import BM25FinderResult
from retrieval.semantic.embedding import EmbeddingFinderResult
from retrieval.tree.progressive import ProgressiveFinderResult
from .intent import intent_priority_score


def adapt_bm25_result(*, method: str, result: BM25FinderResult) -> ProgressiveFinderResult:
    candidates = [
        FinderCandidate(
            rank=hit.rank,
            item_id=hit.choice_id,
            payload=hit.payload,
            branch_path=(
                method,
            ),
            label=hit.choice_id,
            description=hit.description)
        for hit in result.hits
    ]
    return ProgressiveFinderResult(
        candidates=candidates,
        trace=FinderTrace(),
        candidate_records=[
            {"rank": hit.rank, "raw_output": hit.choice_id, "resolved_payload": hit.payload, "valid": True,
                "selected": hit.rank == 1, "choice_id": hit.choice_id, "score": hit.score, "source": method}
            for hit in result.hits
        ],
        summary_lines=[
            (
                f"{hit.rank}. {hit.choice_id} -> {hit.payload} "
                f"(score={hit.score:.4f}, source={method})"
            )
            for hit in result.hits
        ],
        selected_payload=result.hits[0].payload if result.hits else None,
        selected_rank=result.hits[0].rank if result.hits else -1,
        raw_outputs=[],
        request_messages=[{"role": "user", "content": result.query_text}],
        elapsed_ms=result.elapsed_ms,
    )


def adapt_embedding_result(*, method: str, result: EmbeddingFinderResult) -> ProgressiveFinderResult:
    candidates = [
        FinderCandidate(
            rank=hit.rank,
            item_id=hit.choice_id,
            payload=hit.payload,
            branch_path=(
                method,
            ),
            label=hit.choice_id,
            description=hit.description)
        for hit in result.hits
    ]
    return ProgressiveFinderResult(
        candidates=candidates,
        trace=FinderTrace(),
        candidate_records=[
            {"rank": hit.rank, "raw_output": hit.choice_id, "resolved_payload": hit.payload, "valid": True,
                "selected": hit.rank == 1, "choice_id": hit.choice_id, "score": hit.score, "source": method}
            for hit in result.hits
        ],
        summary_lines=[
            (
                f"{hit.rank}. {hit.choice_id} -> {hit.payload} "
                f"(score={hit.score:.4f}, source={method})"
            )
            for hit in result.hits
        ],
        selected_payload=result.hits[0].payload if result.hits else None,
        selected_rank=result.hits[0].rank if result.hits else -1,
        raw_outputs=[],
        request_messages=[{"role": "user", "content": result.query_text}],
        elapsed_ms=result.elapsed_ms,
    )


def augment_progressive_result(
    *,
    progressive_result: ProgressiveFinderResult,
    bm25_result: BM25FinderResult,
    embedding_result: EmbeddingFinderResult | None,
    top_k: int,
) -> ProgressiveFinderResult:
    if top_k <= 1:
        return progressive_result
    progressive_head = list(progressive_result.candidates)[: min(5, top_k)]
    embedding_candidates = []
    if embedding_result is not None:
        embedding_candidates = [
            FinderCandidate(
                rank=hit.rank,
                item_id=hit.choice_id,
                payload=hit.payload,
                branch_path=(
                    "embedding_backfill",
                ),
                label=hit.choice_id,
                description=hit.description)
            for hit in embedding_result.hits
        ]
    bm25_candidates = [
        FinderCandidate(
            rank=hit.rank,
            item_id=hit.choice_id,
            payload=hit.payload,
            branch_path=(
                "bm25_backfill",
            ),
            label=hit.choice_id,
            description=hit.description)
        for hit in bm25_result.hits
    ]
    query_text = str(getattr(bm25_result, "query_text", "") or "")
    backfill_candidates = _merge_backfill_candidates(
        query_text=query_text,
        embedding_candidates=embedding_candidates,
        bm25_candidates=bm25_candidates,
    )
    merged: list[tuple[str, FinderCandidate]] = []
    seen: set[str] = set()

    def append_candidates(source: str, candidates) -> None:
        for candidate in candidates:
            dedupe_key = candidate.payload or candidate.item_id
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append((source, candidate))
            if len(merged) >= top_k:
                return

    append_candidates("progressive", progressive_head)
    append_candidates("backfill", backfill_candidates)
    if not merged:
        return progressive_result

    candidates = [
        FinderCandidate(
            rank=index,
            item_id=candidate.item_id,
            payload=candidate.payload,
            branch_path=candidate.branch_path,
            label=candidate.label,
            description=candidate.description)
        for index, (_source, candidate) in enumerate(merged[:top_k], start=1)
    ]
    candidate_records: list[dict[str, object]] = []
    for candidate, (source, _raw_candidate) in zip(candidates, merged[:top_k]):
        candidate_records.append(
            {
                "rank": candidate.rank,
                "raw_output": candidate.item_id,
                "resolved_payload": candidate.payload,
                "valid": True,
                "selected": candidate.rank == 1,
                "choice_id": candidate.item_id,
                "source": source,
            }
        )
    summary_lines: list[str] = []
    for candidate, (source, _raw_candidate) in zip(candidates, merged[:top_k]):
        summary_lines.append(f"{candidate.rank}. {candidate.item_id} -> {candidate.payload} (source={source})")
    trace = progressive_result.trace
    if trace is not None:
        trace.record(
            "result_backfill",
            node_id="ROOT",
            depth=0,
            detail={
                "progressive_head": len(progressive_head),
                "progressive_candidates": len(
                    progressive_result.candidates),
                "embedding_candidates": len(embedding_candidates),
                "bm25_candidates": len(bm25_candidates),
                "final_candidates": len(candidates)})
    extra_elapsed = float(bm25_result.elapsed_ms) + \
        float(embedding_result.elapsed_ms if embedding_result is not None else 0.0)
    return ProgressiveFinderResult(
        candidates=candidates,
        trace=trace,
        candidate_records=candidate_records,
        summary_lines=summary_lines,
        selected_payload=candidates[0].payload if candidates else None,
        selected_rank=candidates[0].rank if candidates else -1,
        raw_outputs=list(progressive_result.raw_outputs),
        request_messages=list(progressive_result.request_messages),
        elapsed_ms=float(progressive_result.elapsed_ms) + extra_elapsed,
    )


def _rerank_by_intent(query_text: str, candidates: list[FinderCandidate]) -> list[FinderCandidate]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -intent_priority_score(query_text, candidate.item_id, candidate.payload,
                                   candidate.label, candidate.description),
            candidate.rank,
            candidate.payload,
        ),
    )
    return [
        FinderCandidate(
            rank=index,
            item_id=candidate.item_id,
            payload=candidate.payload,
            branch_path=candidate.branch_path,
            label=candidate.label,
            description=candidate.description,
        )
        for index, candidate in enumerate(ranked, start=1)
    ]


def _merge_backfill_candidates(
    *,
    query_text: str,
    embedding_candidates: list[FinderCandidate],
    bm25_candidates: list[FinderCandidate],
) -> list[FinderCandidate]:
    if query_text:
        embedding_candidates = _rerank_by_intent(query_text, embedding_candidates)
        bm25_candidates = _rerank_by_intent(query_text, bm25_candidates)

    merged: dict[str, dict[str, object]] = {}

    def absorb(source: str, candidates: list[FinderCandidate]) -> None:
        for candidate in candidates:
            key = str(candidate.payload or candidate.item_id)
            if not key:
                continue
            entry = merged.setdefault(
                key,
                {
                    "candidate": candidate,
                    "best_rank": int(candidate.rank),
                    "rrf": 0.0,
                    "sources": set(),
                },
            )
            entry["best_rank"] = min(int(entry["best_rank"]), int(candidate.rank))
            entry["rrf"] = float(entry["rrf"]) + (1.0 / (60.0 + max(1, int(candidate.rank))))
            cast_sources = entry["sources"]
            if not isinstance(cast_sources, set):
                raise TypeError("Merged candidate sources must be a set")
            cast_sources.add(source)
            chosen = entry["candidate"]
            if not isinstance(chosen, FinderCandidate):
                raise TypeError("Merged candidate entry must hold a FinderCandidate")
            if len(str(candidate.description or "")) > len(str(chosen.description or "")):
                entry["candidate"] = candidate

    absorb("embedding", embedding_candidates)
    absorb("bm25", bm25_candidates)

    ordered = sorted(
        merged.values(),
        key=lambda entry: (
            -intent_priority_score(
                query_text,
                str(getattr(entry["candidate"], "item_id", "") or ""),
                str(getattr(entry["candidate"], "payload", "") or ""),
                str(getattr(entry["candidate"], "label", "") or ""),
                str(getattr(entry["candidate"], "description", "") or ""),
            ),
            -len(entry["sources"]),
            -float(entry["rrf"]),
            int(entry["best_rank"]),
            str(getattr(entry["candidate"], "payload", "") or ""),
        ),
    )
    return [
        FinderCandidate(
            rank=index,
            item_id=str(entry["candidate"].item_id),
            payload=str(entry["candidate"].payload),
            branch_path=tuple(entry["candidate"].branch_path),
            label=str(entry["candidate"].label),
            description=str(entry["candidate"].description),
        )
        for index, entry in enumerate(ordered, start=1)
    ]
