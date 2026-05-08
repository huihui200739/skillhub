from __future__ import annotations

import time
from typing import Any

from skill_review.domain.check_catalog import list_review_sections
from skill_review.domain.types import ReviewFindingDraft

FINDING_PENALTY = {"high": 25, "medium": 12, "low": 4, "info": 1}


def aggregate_review_result(findings: list[ReviewFindingDraft]) -> dict[str, Any]:
    sections = [_build_section(section, findings) for section in list_review_sections()]
    all_findings = _flatten_section_findings(sections)
    overall = "rejected" if _has_blocking_finding(all_findings) else "approved"
    risk = _derive_risk(all_findings)
    return {
        "review_id": f"rv_{int(time.time() * 1000)}",
        "score": _average_score(sections),
        "overall": overall,
        "risk": risk,
        "conclusion": _build_conclusion(overall, sections),
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics": _build_review_metrics(sections),
        "sections": sections,
    }


def _build_section(section: dict[str, Any], findings: list[ReviewFindingDraft]) -> dict[str, Any]:
    checks = [_build_check(section, check, findings) for check in section["checks"]]
    metrics = _build_section_metrics(checks)
    status = _derive_section_status(checks)
    return {
        "key": section["key"],
        "title": section["title"],
        "summary": section[f"{status}_summary"],
        "recommendation": section["recommendation"],
        "display_status": status,
        "gate_status": "failed" if status == "failed" else "passed",
        "score": _score_findings(_flatten_check_findings(checks)),
        "metrics": metrics,
        "checks": checks,
    }


def _flatten_section_findings(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for section in sections:
        findings.extend(_flatten_check_findings(section["checks"]))
    return findings


def _flatten_check_findings(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for check in checks:
        findings.extend(check["findings"])
    return findings


def _build_check(
    section: dict[str, Any],
    check: dict[str, str],
    findings: list[ReviewFindingDraft],
) -> dict[str, Any]:
    check_findings = [
        _normalize_finding(finding, index)
        for index, finding in enumerate(findings)
        if finding.check_id == check["check_id"]
    ]
    status = _derive_check_status(check_findings)
    return {
        "check_id": check["check_id"],
        "name": check["name"],
        "target": check["target"],
        "scope": check["scope"],
        "status": status,
        "result_summary": _build_check_summary(status, check_findings),
        "recommendation": check["recommendation"],
        "metrics": {
            "finding_count": len(check_findings),
            "high_risk_finding_count": len([item for item in check_findings if item["severity"] == "high"]),
        },
        "findings": check_findings,
    }


def _normalize_finding(finding: ReviewFindingDraft, index: int) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id or f"finding-{index + 1}",
        "source_type": finding.source_type,
        "section_key": finding.section_key,
        "check_id": finding.check_id,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "category": finding.category,
        "capability": finding.capability,
        "title": finding.title,
        "description": finding.description,
        "recommendation": finding.recommendation,
        "gate_recommendation": finding.gate_recommendation,
        "evidence": finding.evidence,
    }


def _derive_check_status(findings: list[dict[str, Any]]) -> str:
    if _has_blocking_finding(findings):
        return "failed"
    if any(finding["severity"] in {"medium", "high"} for finding in findings):
        return "attention"
    return "passed"


def _derive_section_status(checks: list[dict[str, Any]]) -> str:
    statuses = {check["status"] for check in checks}
    if "failed" in statuses:
        return "failed"
    if "attention" in statuses:
        return "attention"
    return "passed"


def _has_blocking_finding(findings: list[dict[str, Any]]) -> bool:
    return any(item["gate_recommendation"] == "block" and item["severity"] == "high" for item in findings)


def _build_check_summary(status: str, findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "未发现该检查项下的阻断或关注风险。"
    if status == "failed":
        return f"发现 {len(findings)} 条需要阻断处理的风险。"
    if status == "attention":
        return f"发现 {len(findings)} 条需要关注的风险。"
    return f"已检查，发现 {len(findings)} 条低风险提示，不影响发布。"


def _build_review_metrics(sections: list[dict[str, Any]]) -> dict[str, int]:
    checks = _flatten_section_checks(sections)
    findings = _flatten_check_findings(checks)
    return {
        "section_count": len(sections),
        "check_count": len(checks),
        "failed_check_count": len([check for check in checks if check["status"] == "failed"]),
        "attention_check_count": len([check for check in checks if check["status"] == "attention"]),
        "passed_check_count": len([check for check in checks if check["status"] == "passed"]),
        "finding_count": len(findings),
        "high_risk_finding_count": len([item for item in findings if item["severity"] == "high"]),
    }


def _build_section_metrics(checks: list[dict[str, Any]]) -> dict[str, int]:
    findings = _flatten_check_findings(checks)
    return {
        "check_count": len(checks),
        "failed_check_count": len([check for check in checks if check["status"] == "failed"]),
        "attention_check_count": len([check for check in checks if check["status"] == "attention"]),
        "passed_check_count": len([check for check in checks if check["status"] == "passed"]),
        "finding_count": len(findings),
        "high_risk_finding_count": len([item for item in findings if item["severity"] == "high"]),
    }


def _flatten_section_checks(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for section in sections:
        checks.extend(section["checks"])
    return checks


def _score_findings(findings: list[dict[str, Any]]) -> int:
    penalty = sum(FINDING_PENALTY.get(finding["severity"], 1) for finding in findings)
    return max(0, 100 - penalty)


def _average_score(sections: list[dict[str, Any]]) -> int:
    if not sections:
        return 0
    return round(sum(section["score"] for section in sections) / len(sections))


def _derive_risk(findings: list[dict[str, Any]]) -> str:
    if any(item["severity"] == "high" for item in findings):
        return "high"
    if any(item["severity"] == "medium" for item in findings):
        return "medium"
    return "low"


def _build_conclusion(overall: str, sections: list[dict[str, Any]]) -> str:
    failed_sections = [section["title"] for section in sections if section["display_status"] == "failed"]
    attention_sections = [section["title"] for section in sections if section["display_status"] == "attention"]
    if overall == "rejected":
        return f"审查未通过，请先修复 {'、'.join(failed_sections)} 中的问题后重新提交。"
    if attention_sections:
        return f"审查通过，但 {'、'.join(attention_sections)} 存在需关注项，建议确认。"
    return "审查通过，当前提交可以进入发布流程。"
