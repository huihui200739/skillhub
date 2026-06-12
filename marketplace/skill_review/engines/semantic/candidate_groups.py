from __future__ import annotations

import re
from dataclasses import dataclass

from skill_review.domain.types import ReviewFindingDraft


@dataclass(frozen=True)
class SemanticCandidateGroupItem:
    finding: ReviewFindingDraft
    finding_ref: str


@dataclass(frozen=True)
class SemanticCandidateGroup:
    representative: SemanticCandidateGroupItem
    members: list[SemanticCandidateGroupItem]


def group_semantic_candidate_items(
    items: list[SemanticCandidateGroupItem],
) -> list[SemanticCandidateGroup]:
    grouped_items: dict[str, SemanticCandidateGroup] = {}
    groups: list[SemanticCandidateGroup] = []

    for item in items:
        group_key = build_semantic_candidate_group_key(item.finding)
        if not group_key:
            groups.append(SemanticCandidateGroup(representative=item, members=[item]))
            continue
        existing_group = grouped_items.get(group_key)
        if existing_group:
            existing_group.members.append(item)
            continue
        group = SemanticCandidateGroup(representative=item, members=[item])
        grouped_items[group_key] = group
        groups.append(group)
    return groups


def build_semantic_candidate_review_alias_map(
    items: list[SemanticCandidateGroupItem],
) -> dict[str, str]:
    alias_by_finding_ref: dict[str, str] = {}
    for group in group_semantic_candidate_items(items):
        representative_ref = group.representative.finding_ref
        for member in group.members:
            alias_by_finding_ref[member.finding_ref] = representative_ref
    return alias_by_finding_ref


def build_semantic_candidate_group_key(finding: ReviewFindingDraft) -> str:
    source_evidence = primary_source_evidence(finding)
    if not source_evidence:
        return ""
    evidence_key = normalize_group_text(str(source_evidence.get("text") or ""))
    if not evidence_key:
        return ""
    return "|".join(
        [
            finding.source_type,
            finding.section_key,
            finding.check_id,
            normalize_group_text(finding.category),
            normalize_group_text(finding.capability),
            normalize_group_text(finding.title),
            evidence_key,
        ]
    )


def primary_source_evidence(finding: ReviewFindingDraft) -> dict | None:
    for evidence in finding.evidence:
        if evidence.get("evidence_type") == "source":
            return evidence
    return None


def normalize_group_text(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"\\([|`*_{}\[\]()#+.!-])", r"\1", text)
    return re.sub(r"\s+", " ", text)
