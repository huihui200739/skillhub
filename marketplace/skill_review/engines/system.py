from __future__ import annotations

from skill_review.domain.check_catalog import get_review_check
from skill_review.domain.types import ReviewFindingDraft
from skill_review.engines.types import ReviewEngine, ReviewEngineContext, ReviewEngineResult
from skill_review.runtime.package_access.file_catalog import is_skill_file


def _run_system_engine(context: ReviewEngineContext) -> ReviewEngineResult:
    findings: list[ReviewFindingDraft] = []
    files = context.package_summary.files
    if not files:
        findings.append(
            _system_finding(
                "package_structure_integrity",
                "high",
                "压缩包中没有可检查文件",
                "压缩包中没有可供审查的文件。",
            )
        )
    if not any(is_skill_file(file.path) for file in files):
        findings.append(
            _system_finding(
                "main_doc_presence",
                "high",
                "缺少主说明文件",
                "Skill 包中未检测到 SKILL.md。",
            )
        )
    for warning in context.package_summary.warnings:
        findings.append(
            _system_finding(
                "package_structure_integrity",
                "medium",
                "包检查提示",
                warning,
            )
        )
    return ReviewEngineResult(
        engine_id="system",
        engine_name="System Checks",
        kind="system",
        coverage=[],
        findings=findings,
        raw_output={"finding_count": len(findings)},
    )


def _system_finding(check_id: str, severity: str, title: str, description: str) -> ReviewFindingDraft:
    check = get_review_check(check_id)
    return ReviewFindingDraft(
        source_type="system",
        section_key="auditability",
        check_id=check_id,
        severity=severity,  # type: ignore[arg-type]
        confidence="high",
        category="spec_violation",
        capability=check_id,
        title=title,
        description=description,
        recommendation=check["recommendation"],
        gate_recommendation="block" if severity == "high" else "attention",
        evidence=[{"evidence_type": "system", "text": description}],
    )


system_engine = ReviewEngine(
    engine_id="system",
    engine_name="System Checks",
    kind="system",
    failure_policy="abort_review",
    run=_run_system_engine,
)
