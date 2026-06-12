from __future__ import annotations

import re
from typing import Any

from skill_review.domain.types import ReviewFindingDraft
from skill_review.runtime.semantic_context.types import SemanticContext, SemanticSample

CONTEXT_RADIUS = 2


def build_rule_finding_source_context(
    finding: ReviewFindingDraft,
    semantic_context: SemanticContext,
) -> dict[str, Any] | None:
    evidence = find_source_evidence_with_file(finding)
    if not evidence:
        return None
    sample = find_sample_for_evidence(semantic_context, evidence)
    if not sample:
        return None
    lines = split_lines(sample.preview)
    line_index = find_evidence_line_index(lines, evidence, sample)
    if line_index is None:
        return None
    location = evidence.get("location") if isinstance(evidence.get("location"), dict) else {}
    return {
        "file": location.get("file") or sample.path,
        **positive_line_number(location.get("line")),
        "heading_path": collect_heading_path(lines, line_index),
        "in_code_block": is_inside_code_block(lines, line_index),
        "before": collect_previous_lines(lines, line_index, CONTEXT_RADIUS),
        "evidence_line": lines[line_index].strip() if line_index < len(lines) else "",
        "after": collect_following_lines(lines, line_index, CONTEXT_RADIUS),
    }


def find_source_evidence_with_file(finding: ReviewFindingDraft) -> dict | None:
    for evidence in finding.evidence:
        location = evidence.get("location") if isinstance(evidence.get("location"), dict) else None
        if evidence.get("evidence_type") == "source" and isinstance(location, dict) and location.get("file"):
            return evidence
    return None


def find_sample_for_evidence(semantic_context: SemanticContext, evidence: dict) -> SemanticSample | None:
    location = evidence.get("location") if isinstance(evidence.get("location"), dict) else {}
    evidence_file = normalize_path(str(location.get("file") or ""))
    for sample in semantic_context.samples:
        if normalize_path(sample.path) == evidence_file:
            return sample
    return None


def split_lines(value: str) -> list[str]:
    return re.split(r"\r?\n", value)


def find_evidence_line_index(lines: list[str], evidence: dict, sample: SemanticSample) -> int | None:
    text_match = find_line_by_evidence_text(lines, str(evidence.get("text") or ""))
    if text_match is not None:
        return text_match
    if sample.truncated:
        return None
    location = evidence.get("location") if isinstance(evidence.get("location"), dict) else {}
    line_number = location.get("line")
    if not isinstance(line_number, int) or line_number < 1 or line_number > len(lines):
        return None
    return line_number - 1


def find_line_by_evidence_text(lines: list[str], evidence_text: str) -> int | None:
    target = normalize_text(select_evidence_anchor(evidence_text))
    if not target:
        return None
    for index, line in enumerate(lines):
        normalized_line = normalize_text(line)
        if not normalized_line:
            continue
        if normalized_line in target or target in normalized_line:
            return index
    return None


def select_evidence_anchor(evidence_text: str) -> str:
    for line in split_lines(evidence_text):
        stripped = line.strip()
        if stripped:
            return stripped
    return evidence_text.strip()


def collect_heading_path(lines: list[str], line_index: int) -> list[str]:
    headings: list[str] = []
    for index in range(line_index):
        heading = parse_markdown_heading(lines[index])
        if not heading:
            continue
        level, title = heading
        del headings[level - 1:]
        headings.extend([""] * (level - len(headings)))
        headings[level - 1] = title
    return [heading for heading in headings if heading]


def parse_markdown_heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
    if not match:
        return None
    return len(match.group(1)), re.sub(r"\s+#+$", "", match.group(2)).strip()


def is_inside_code_block(lines: list[str], line_index: int) -> bool:
    inside_code_block = False
    for index in range(line_index + 1):
        if re.match(r"^\s*```", lines[index]):
            inside_code_block = not inside_code_block
    return inside_code_block


def collect_previous_lines(lines: list[str], line_index: int, max_lines: int) -> list[str]:
    collected: list[str] = []
    for index in range(line_index - 1, -1, -1):
        if len(collected) >= max_lines:
            break
        line = lines[index].strip()
        if line:
            collected.insert(0, line)
    return collected


def collect_following_lines(lines: list[str], line_index: int, max_lines: int) -> list[str]:
    collected: list[str] = []
    for index in range(line_index + 1, len(lines)):
        if len(collected) >= max_lines:
            break
        line = lines[index].strip()
        if line:
            collected.append(line)
    return collected


def positive_line_number(value: Any) -> dict[str, int]:
    return {"line": value} if isinstance(value, int) and value > 0 else {}


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("/")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("…", "")).strip()
