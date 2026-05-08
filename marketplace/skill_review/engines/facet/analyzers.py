from __future__ import annotations

from skill_review.domain.system_facets import SkillCapabilityFact, SkillCapabilityInventory, SkillSystemFacets
from skill_review.engines.facet.coverage_gap import fact_affects_area
from skill_review.engines.facet.network_scope import classify_endpoint

NETWORK_ACCESS_KINDS = [
    ("package_source", "package_source_access"),
    ("artifact_source", "artifact_source_access"),
    ("external_service", "external_service_access"),
]


def build_skill_system_facets(inventory: SkillCapabilityInventory) -> SkillSystemFacets:
    dependency = analyze_dependency_facet(inventory)
    network = analyze_network_facet(inventory)
    return {
        "system_tags": sorted(set([*dependency["system_tags"], *network["system_tags"]])),
        "external_network": network["external_network"],
        "third_party_dependencies": dependency["third_party_dependencies"],
        "coverage": [*dependency["coverage"], *network["coverage"]],
    }


def analyze_dependency_facet(inventory: SkillCapabilityInventory) -> dict:
    facts = [
        fact
        for fact in inventory["facts"]
        if fact["kind"] in {"dependency_declaration", "package_import", "dynamic_install_command"}
    ]
    coverage_gaps = collect_coverage_gap_facts(inventory, "third_party_dependencies")
    status = resolve_dependency_status(facts, len(coverage_gaps))
    details = [detail for fact in facts for detail in build_dependency_detail(fact)]
    return {
        "system_tags": append_analysis_incomplete_tag(build_dependency_tags(status), len(coverage_gaps)),
        "third_party_dependencies": {
            "status": status,
            "confidence": resolve_facet_confidence(facts, len(coverage_gaps)),
            "inventory_sources": sorted({fact["source"] for fact in facts}),
            "dependency_scope_summary": sorted({detail["scope"] for detail in details}),
            "details": details,
        },
        "coverage": [
            {
                "analyzer_id": "dependency-facet",
                "status": resolve_facet_coverage_status(len(facts), len(coverage_gaps)),
                **coverage_reason(coverage_gaps),
            }
        ],
    }


def analyze_network_facet(inventory: SkillCapabilityInventory) -> dict:
    requirements = collect_network_requirements(inventory["facts"])
    coverage_gaps = collect_coverage_gap_facts(inventory, "external_network")
    status = resolve_network_status(
        len(requirements),
        any(item["requiredness"] == "required" for item in requirements),
        len(coverage_gaps),
    )
    subfacets = {}
    for kind, field in NETWORK_ACCESS_KINDS:
        kind_requirements = [item for item in requirements if item["access_kind"] == kind]
        subfacets[field] = {
            "status": resolve_network_status(
                len(kind_requirements),
                any(item["requiredness"] == "required" for item in kind_requirements),
                len(coverage_gaps),
            ),
            "confidence": resolve_network_confidence(kind_requirements, len(coverage_gaps)),
            "details": [to_network_detail(item) for item in kind_requirements],
        }
    return {
        "system_tags": append_analysis_incomplete_tag(build_network_tags(status), len(coverage_gaps)),
        "external_network": {
            "status": status,
            "confidence": resolve_network_confidence(requirements, len(coverage_gaps)),
            "details": [to_network_detail(item) for item in requirements],
            **subfacets,
        },
        "coverage": [
            {
                "analyzer_id": "network-facet",
                "status": resolve_facet_coverage_status(len(requirements), len(coverage_gaps)),
                **coverage_reason(coverage_gaps),
            }
        ],
    }


def resolve_dependency_status(facts: list[SkillCapabilityFact], coverage_gap_count: int) -> str:
    if any(fact["kind"] == "dynamic_install_command" for fact in facts):
        return "dynamic_install_detected"
    if any(fact["kind"] == "dependency_declaration" for fact in facts):
        return "present"
    if any(fact["kind"] == "package_import" for fact in facts):
        return "possible"
    if coverage_gap_count > 0:
        return "unknown"
    return "none_detected"


def build_dependency_detail(fact: SkillCapabilityFact) -> list[dict]:
    dependency = fact.get("dependency")
    if dependency:
        return [
            {
                "package_name": dependency["package_name"],
                "ecosystem": dependency["ecosystem"],
                "scope": dependency["scope"],
                "evidence": fact["evidence"],
                "confidence": fact["confidence"],
            }
        ]
    import_fact = fact.get("import")
    if not import_fact or fact["kind"] != "package_import":
        return []
    return [
        {
            "package_name": fact["subject"],
            "ecosystem": "pypi" if import_fact["language"] == "python" else "npm",
            "scope": "runtime",
            "evidence": fact["evidence"],
            "confidence": fact["confidence"],
        }
    ]


def build_dependency_tags(status: str) -> list[str]:
    if status == "dynamic_install_detected":
        return ["has_third_party_dependencies", "dynamic_dependency_install"]
    if status == "present":
        return ["has_third_party_dependencies"]
    return []


def collect_network_requirements(facts: list[SkillCapabilityFact]) -> list[dict]:
    requirements: list[dict] = []
    for fact in facts:
        if fact["kind"] == "network_call" and fact.get("network_scope") != "loopback":
            requirements.append(network_call_requirement(fact))
            continue
        dependency_requirement = dependency_source_requirement(fact)
        if dependency_requirement:
            requirements.append(dependency_requirement)
    return requirements


def network_call_requirement(fact: SkillCapabilityFact) -> dict:
    network = fact.get("network") or {}
    return {
        "requiredness": network.get("requiredness", "unknown"),
        "phase": fact.get("phase", "unknown"),
        "network_scope": fact.get("network_scope", "unknown"),
        "access_kind": network.get("access_kind", "unknown"),
        "evidence": fact["evidence"],
        "confidence": fact["confidence"],
    }


def dependency_source_requirement(fact: SkillCapabilityFact) -> dict | None:
    if fact["kind"] not in {"dependency_declaration", "dynamic_install_command"}:
        return None
    dependency = fact.get("dependency")
    if not dependency:
        return None
    access_kind = dependency_source_access_kind(dependency["source_kind"])
    if access_kind == "unknown":
        return None
    return {
        "requiredness": "required",
        "phase": fact.get("phase", "install"),
        "network_scope": dependency_network_scope(dependency.get("registry_url")),
        "access_kind": access_kind,
        "evidence": fact["evidence"],
        "confidence": fact["confidence"],
    }


def dependency_source_access_kind(source_kind: str) -> str:
    if source_kind in {"registry_default", "registry_custom"}:
        return "package_source"
    if source_kind in {"git", "direct_url"}:
        return "artifact_source"
    return "unknown"


def dependency_network_scope(registry_url: str | None) -> str:
    if not registry_url:
        return "public"
    if reuses_http_scheme(registry_url):
        return classify_endpoint(registry_url)
    return "unknown"


def resolve_network_status(fact_count: int, required: bool, coverage_gap_count: int) -> str:
    if required:
        return "required"
    if fact_count == 0 and coverage_gap_count > 0:
        return "unknown"
    return "possible" if fact_count > 0 else "none_detected"


def build_network_tags(status: str) -> list[str]:
    if status == "required":
        return ["requires_external_network"]
    if status == "possible":
        return ["uses_external_network"]
    return []


def resolve_network_confidence(requirements: list[dict], coverage_gap_count: int) -> str:
    if not requirements and coverage_gap_count > 0:
        return "low"
    if any(item["confidence"] == "high" for item in requirements):
        return "high"
    if requirements:
        return "medium"
    return "high"


def to_network_detail(requirement: dict) -> dict:
    return {
        "phase": requirement["phase"],
        "network_scope": requirement["network_scope"],
        "access_kind": requirement["access_kind"],
        "evidence": requirement["evidence"],
        "confidence": requirement["confidence"],
    }


def collect_coverage_gap_facts(inventory: SkillCapabilityInventory, area: str) -> list[SkillCapabilityFact]:
    return [fact for fact in inventory["facts"] if fact_affects_area(fact, area)]


def resolve_facet_confidence(facts: list[SkillCapabilityFact], coverage_gap_count: int) -> str:
    if not facts and coverage_gap_count > 0:
        return "low"
    if any(fact["confidence"] == "high" for fact in facts):
        return "high"
    if facts:
        return "medium"
    return "high"


def resolve_facet_coverage_status(fact_count: int, coverage_gap_count: int) -> str:
    if coverage_gap_count > 0:
        return "partial" if fact_count > 0 else "unknown"
    return "completed"


def append_analysis_incomplete_tag(tags: list[str], coverage_gap_count: int) -> list[str]:
    return [*tags, "analysis_incomplete"] if coverage_gap_count > 0 else tags


def coverage_reason(coverage_gaps: list[SkillCapabilityFact]) -> dict[str, str]:
    if not coverage_gaps:
        return {}
    return {"reason": coverage_gaps[0].get("coverage_gap", {}).get("reason", "analysis_incomplete")}


def reuses_http_scheme(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))
