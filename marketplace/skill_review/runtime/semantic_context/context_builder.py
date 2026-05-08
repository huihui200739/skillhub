from __future__ import annotations

from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any

from skill_review.domain.types import PackageFileEntry, ReviewFindingDraft
from skill_review.runtime.package_access.base import PackageAccess
from skill_review.runtime.package_access.file_catalog import (
    compare_review_relevant_paths,
    is_text_like_file,
    score_review_relevant_path,
)
from skill_review.runtime.semantic_context.types import SemanticContext, SemanticSample


@dataclass(frozen=True)
class SemanticContextBudget:
    max_candidate_files: int = 200
    max_sample_files: int = 16
    max_bytes_per_sample: int = 256 * 1024
    max_chars_per_sample: int = 3000
    max_chars_per_main_document: int = 30000
    max_total_chars: int = 40000


def build_semantic_context(
    *,
    access: PackageAccess,
    focus_findings: list[ReviewFindingDraft] | None = None,
    budget: SemanticContextBudget | None = None,
) -> SemanticContext:
    resolved_budget = budget or SemanticContextBudget()
    focus_evidence = collect_source_evidence(focus_findings or [])
    text_files = sorted(
        [file for file in access.list_files() if is_text_like_file(file.path)],
        key=cmp_to_key(lambda left, right: compare_review_relevant_paths(left.path, right.path)),
    )
    candidate_files = select_candidate_files(text_files, focus_evidence)[: resolved_budget.max_candidate_files]
    samples: list[SemanticSample] = []
    remaining_chars = resolved_budget.max_total_chars

    for file in candidate_files:
        if len(samples) >= resolved_budget.max_sample_files or remaining_chars <= 0:
            break
        read_result = access.read_text_file(file.path, max_bytes=resolved_budget.max_bytes_per_sample)
        if read_result.status != "success" or read_result.content is None:
            continue
        preview_char_limit = min(resolve_sample_char_limit(file.path, resolved_budget), remaining_chars)
        preview = build_sample_preview(
            read_result.content,
            preview_char_limit,
            [item for item in focus_evidence if is_same_path(item.get("location", {}).get("file", ""), file.path)],
        )
        if not preview:
            continue
        samples.append(
            SemanticSample(
                path=file.path,
                size=file.size,
                preview=preview,
                truncated=read_result.truncated or len(read_result.content) > preview_char_limit,
                priority=score_review_relevant_path(file.path),
            )
        )
        remaining_chars -= len(preview)
    return SemanticContext(samples=samples)


def collect_source_evidence(findings: list[ReviewFindingDraft]) -> list[dict[str, Any]]:
    source_evidence: list[dict[str, Any]] = []
    for finding in findings:
        for evidence in finding.evidence:
            location = evidence.get("location") if isinstance(evidence, dict) else None
            if evidence.get("evidence_type") == "source" and isinstance(location, dict) and location.get("file"):
                source_evidence.append(evidence)
    return source_evidence


def select_candidate_files(
    files: list[PackageFileEntry],
    focus_evidence: list[dict[str, Any]],
) -> list[PackageFileEntry]:
    by_path = {normalize_path(file.path): file for file in files}
    selected: dict[str, PackageFileEntry] = {}
    for file in [item for item in files if is_main_skill_document(item.path)]:
        selected[normalize_path(file.path)] = file
    for evidence in focus_evidence:
        file = by_path.get(normalize_path(evidence.get("location", {}).get("file", "")))
        if file:
            selected[normalize_path(file.path)] = file
    for file in files:
        selected[normalize_path(file.path)] = file
    return list(selected.values())


def resolve_sample_char_limit(file_path: str, budget: SemanticContextBudget) -> int:
    if is_main_skill_document(file_path):
        main_document_budget = budget.max_total_chars * 3 // 4
        return max(
            budget.max_chars_per_sample,
            min(budget.max_chars_per_main_document, main_document_budget),
        )
    return budget.max_chars_per_sample


def build_sample_preview(
    raw_text: str,
    max_chars: int,
    focus_evidence: list[dict[str, Any]] | None = None,
) -> str:
    if len(raw_text) <= max_chars:
        return raw_text
    focus_excerpt = build_focus_excerpt(raw_text, focus_evidence or [], max_chars // 2)
    if focus_excerpt:
        separator = "\n...\n"
        generic_budget = max(0, max_chars - len(focus_excerpt) - len(separator))
        return separator.join(
            [item for item in [build_generic_sample_preview(raw_text, generic_budget), focus_excerpt] if item]
        )
    return build_generic_sample_preview(raw_text, max_chars)


def build_generic_sample_preview(raw_text: str, max_chars: int) -> str:
    separator = "\n...\n"
    separator_budget = len(separator) * 2
    if max_chars <= separator_budget + 120:
        return raw_text[:max_chars]
    available_chars = max_chars - separator_budget
    head_chars = min(4000, available_chars // 2)
    middle_chars = available_chars // 4
    tail_chars = available_chars - head_chars - middle_chars
    middle_start = max(head_chars, (len(raw_text) - middle_chars) // 2)
    tail_start = max(middle_start + middle_chars, len(raw_text) - tail_chars)
    return separator.join(
        [
            raw_text[:head_chars],
            raw_text[middle_start:middle_start + middle_chars],
            raw_text[tail_start:],
        ]
    )


def build_focus_excerpt(raw_text: str, focus_evidence: list[dict[str, Any]], max_chars: int) -> str:
    if max_chars <= 0 or not focus_evidence:
        return ""
    lines = raw_text.splitlines()
    excerpts: list[str] = []
    for evidence in focus_evidence:
        excerpt = build_line_excerpt(lines, evidence)
        if not excerpt:
            continue
        next_excerpt = "\n...\n".join([*excerpts, excerpt])
        if len(next_excerpt) > max_chars:
            break
        excerpts.append(excerpt)
    return "\n...\n".join(excerpts)


def build_line_excerpt(lines: list[str], evidence: dict[str, Any]) -> str:
    line_number = evidence.get("location", {}).get("line")
    if not isinstance(line_number, int) or line_number < 1:
        return ""
    line_index = line_number - 1
    if line_index >= len(lines):
        return ""
    start = max(0, line_index - 3)
    end = min(len(lines), line_index + 4)
    return "\n".join(lines[start:end])


def is_main_skill_document(file_path: str) -> bool:
    return normalize_path(file_path).endswith("skill.md")


def is_same_path(left: str, right: str) -> bool:
    return normalize_path(left) == normalize_path(right)


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("/")
