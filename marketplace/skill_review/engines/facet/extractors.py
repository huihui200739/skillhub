from __future__ import annotations

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.facet.code_extractors import (
    extract_js_ts_code_facts,
    extract_python_code_facts,
)
from skill_review.engines.facet.command_extractors import extract_command_facts
from skill_review.engines.facet.dependency_extractors import (
    extract_node_dependency_facts,
    extract_python_dependency_facts,
)
from skill_review.engines.facet.documentation_extractors import extract_documentation_facts
from skill_review.engines.facet.unsupported_ecosystem import collect_unsupported_ecosystem_facts
from skill_review.runtime.package_access.base import PackageAccess


def extract_skill_capability_facts(
    package_access: PackageAccess,
    selected_files: list[PackageFileEntry],
    all_files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    facts.extend(extract_documentation_facts(package_access, selected_files))
    facts.extend(extract_node_dependency_facts(package_access, selected_files))
    facts.extend(extract_python_dependency_facts(package_access, selected_files))
    facts.extend(extract_js_ts_code_facts(package_access, selected_files))
    facts.extend(extract_python_code_facts(package_access, selected_files))
    facts.extend(extract_command_facts(package_access, selected_files))
    facts.extend(collect_unsupported_ecosystem_facts(all_files))
    return facts
