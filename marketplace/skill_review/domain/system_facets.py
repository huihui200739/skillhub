from __future__ import annotations

from typing import Any, Literal

SkillFacetConfidence = Literal["high", "medium", "low"]
SkillCapabilityFactKind = Literal[
    "dependency_declaration",
    "package_import",
    "dynamic_install_command",
    "network_call",
    "endpoint",
    "coverage_gap",
]
SkillCapabilityFactSource = Literal[
    "lockfile",
    "manifest",
    "ast_import",
    "ast_sink_with_literal_endpoint",
    "ast_sink_dynamic",
    "command",
    "command_with_endpoint",
    "entrypoint",
    "documentation",
    "scanner_supplement",
    "coverage",
]
SkillFacetPhase = Literal["install", "runtime", "both", "unknown"]
SkillNetworkScope = Literal["loopback", "private_non_loopback", "public", "unknown"]
SkillNetworkAccessKind = Literal["package_source", "artifact_source", "external_service", "unknown"]
SkillNetworkAccessStatus = Literal["none_detected", "possible", "required", "unknown"]
SkillDependencyScope = Literal["runtime", "dev", "build", "optional", "unknown"]
SkillDependencySourceKind = Literal[
    "registry_default",
    "registry_custom",
    "git",
    "direct_url",
    "local_path",
    "editable_local",
    "unknown",
]
SkillFacetArea = Literal["external_network", "third_party_dependencies"]
SkillCoverageGapReason = Literal[
    "unsupported_ecosystem",
    "parse_failed",
    "file_too_large",
    "dynamic_construct",
    "unsupported_format",
    "scan_budget_exceeded",
]
SkillSystemTag = Literal[
    "requires_external_network",
    "uses_external_network",
    "has_third_party_dependencies",
    "dynamic_dependency_install",
    "analysis_incomplete",
]

SkillFacetEvidence = dict[str, Any]
SkillCapabilityFact = dict[str, Any]
MergedSkillCapabilityFact = dict[str, Any]
SkillSystemFacets = dict[str, Any]
SkillCapabilityInventory = dict[str, Any]
