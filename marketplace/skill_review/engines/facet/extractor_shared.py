from __future__ import annotations

import re

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.engines.facet.fact_builders import build_network_call_fact

READ_MAX_BYTES = 512 * 1024
COMMAND_READ_MAX_BYTES = 256 * 1024


def build_runtime_network_fact(
    file_path: str,
    line: int,
    sink: str,
    endpoint: str,
    extractor_id: str,
) -> SkillCapabilityFact:
    return build_network_call_fact(
        extractor_id=extractor_id,
        source="ast_sink_with_literal_endpoint",
        confidence="high",
        subject=endpoint,
        phase="runtime",
        file_path=file_path,
        line=line,
        text=f"{sink}({endpoint})",
        sink=sink,
        endpoint_expression=endpoint,
        evidence_endpoint=endpoint,
        requiredness="required",
        access_kind="external_service",
    )


def extract_url_constants(content: str, pattern: re.Pattern[str]) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in pattern.finditer(content)}


def line_of(content: str, index: int) -> int:
    return content[: max(0, index)].count("\n") + 1
