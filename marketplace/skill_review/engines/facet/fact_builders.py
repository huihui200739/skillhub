from __future__ import annotations

from typing import Any

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.engines.facet.fact_id import build_fact_id, build_merge_key
from skill_review.engines.facet.network_scope import classify_endpoint


def build_network_call_fact(
    *,
    extractor_id: str,
    source: str,
    confidence: str,
    subject: str,
    phase: str,
    file_path: str,
    line: int,
    text: str,
    sink: str,
    endpoint_expression: str,
    requiredness: str,
    access_kind: str,
    network_scope: str | None = None,
    merge_subject: str | None = None,
    evidence_endpoint: str | None = None,
) -> SkillCapabilityFact:
    resolved_scope = network_scope or classify_endpoint(endpoint_expression)
    evidence: dict[str, Any] = {"file": file_path, "line": line, "text": text, "sink": sink}
    if evidence_endpoint:
        evidence["endpoint"] = evidence_endpoint
    return {
        "fact_id": build_fact_id(
            kind="network_call",
            extractor_id=extractor_id,
            subject=subject,
            file=file_path,
            line=line,
        ),
        "merge_key": build_merge_key(
            kind="network_call",
            subject=merge_subject or subject,
            phase=phase,
            scope=resolved_scope,
        ),
        "kind": "network_call",
        "source": source,
        "confidence": confidence,
        "subject": subject,
        "phase": phase,
        "network_scope": resolved_scope,
        "evidence": [evidence],
        "network": {
            "sink": sink,
            "endpoint_expression": endpoint_expression,
            "requiredness": requiredness,
            "access_kind": access_kind,
        },
    }


def build_package_import_fact(
    *,
    extractor_id: str,
    package_name: str,
    module_name: str,
    ecosystem: str,
    language: str,
    file_path: str,
    line: int,
) -> SkillCapabilityFact:
    return {
        "fact_id": build_fact_id(
            kind="package_import",
            extractor_id=extractor_id,
            subject=package_name,
            file=file_path,
            line=line,
        ),
        "merge_key": build_merge_key(
            kind="dependency_declaration",
            subject=f"{ecosystem}:{package_name}",
            phase="runtime",
            scope="runtime",
        ),
        "kind": "package_import",
        "source": "ast_import",
        "confidence": "medium",
        "subject": package_name,
        "phase": "runtime",
        "evidence": [{"file": file_path, "line": line, "text": module_name}],
        "import": {
            "language": language,
            "module_name": module_name,
            "classification": "third_party_candidate",
        },
    }


def build_dependency_fact(
    *,
    extractor_id: str,
    source: str,
    confidence: str,
    package_name: str,
    ecosystem: str,
    scope: str,
    source_kind: str,
    text: str,
    file_path: str,
    line: int,
    kind: str = "dependency_declaration",
    phase: str = "runtime",
    merge_subject: str | None = None,
    version_spec: str | None = None,
    registry_url: str | None = None,
) -> SkillCapabilityFact:
    dependency = {
        "ecosystem": ecosystem,
        "package_name": package_name,
        "scope": scope,
        "direct": True,
        "source_kind": source_kind,
    }
    if version_spec is not None:
        dependency["version_spec"] = version_spec
    if registry_url:
        dependency["registry_url"] = registry_url
    return {
        "fact_id": build_fact_id(
            kind=kind,
            extractor_id=extractor_id,
            subject=package_name,
            file=file_path,
            line=line,
        ),
        "merge_key": build_merge_key(
            kind=kind,
            subject=merge_subject or f"{ecosystem}:{package_name}",
            phase=phase,
            scope=scope,
        ),
        "kind": kind,
        "source": source,
        "confidence": confidence,
        "subject": package_name,
        "phase": phase,
        "evidence": [{"file": file_path, "line": line, "text": text}],
        "dependency": dependency,
    }
