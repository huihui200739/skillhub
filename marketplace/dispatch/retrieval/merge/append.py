from __future__ import annotations

from typing import Dict, List, Sequence

from models.retrieval import RetrieverCandidate, RetrieverTrace
from retrieval.tree.types import ProgressiveRetrieverResult

from ..service.models import RetrieverSearchResult


def hits_to_search_result(
    *,
    method: str,
    source: str,
    elapsed_ms: float,
    trace_events: List[Dict[str, object]],
    candidate_records: List[Dict[str, object]],
) -> RetrieverSearchResult:
    payloads = [str(record.get("resolved_payload") or "") for record in candidate_records]
    return RetrieverSearchResult(
        method=method,
        payloads=payloads,
        candidate_records=candidate_records,
        summary_lines=[
            f"{index}. {record.get('choice_id') or record.get('raw_output') or ''} -> {record.get('resolved_payload') or ''} (source={source})"
            for index, record in enumerate(candidate_records, start=1)
        ],
        selected_payload=payloads[0] if payloads else None,
        selected_rank=1 if payloads else -1,
        elapsed_ms=float(elapsed_ms),
        trace_events=trace_events,
    )


__all__ = [
    "hits_to_search_result",
]
