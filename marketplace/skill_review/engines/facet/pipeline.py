from __future__ import annotations

from skill_review.domain.system_facets import SkillCapabilityInventory, SkillSystemFacets
from skill_review.engines.facet.analyzers import build_skill_system_facets
from skill_review.engines.facet.coverage_gap import build_package_summary_coverage_gap_facts
from skill_review.engines.facet.extractors import extract_skill_capability_facts
from skill_review.engines.facet.fact_merger import merge_skill_capability_facts
from skill_review.engines.facet.scan_budget import plan_skill_facet_scan
from skill_review.runtime.package_access.base import PackageAccess
from skill_review.runtime.package_summary.types import PackageSummary


def run_skill_facet_pipeline(
    package_access: PackageAccess,
    package_summary: PackageSummary,
) -> tuple[SkillSystemFacets, SkillCapabilityInventory]:
    all_files = package_access.list_files()
    scan_plan = plan_skill_facet_scan(all_files)
    facts = [
        *build_package_summary_coverage_gap_facts(package_summary),
        *scan_plan.coverage_gap_facts,
        *extract_skill_capability_facts(package_access, scan_plan.files, all_files),
    ]
    inventory = {
        "facts": facts,
        "merged_facts": merge_skill_capability_facts(facts),
        "scan": {
            "eligible_file_count": len(all_files),
            "scanned_file_count": len(scan_plan.files),
            "selected_bytes": scan_plan.selected_bytes,
            "skipped_by_file_count": scan_plan.skipped_by_file_count,
            "skipped_by_byte_budget": scan_plan.skipped_by_byte_budget,
        },
    }
    return build_skill_system_facets(inventory), inventory
