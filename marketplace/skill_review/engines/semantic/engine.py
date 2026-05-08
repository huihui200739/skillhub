from __future__ import annotations

from skill_review.aggregation.finding_mapper import serialize_review_finding
from skill_review.domain.types import ReviewFindingDraft
from skill_review.engines.semantic.adjudication import (
    normalize_semantic_candidate_review_dispositions,
    read_semantic_candidate_reviews,
)
from skill_review.engines.semantic.contract import (
    DEFAULT_SEMANTIC_RECOMMENDATION,
    SEMANTIC_RESPONSE_JSON_SCHEMA,
)
from skill_review.engines.semantic.observation_planner import build_semantic_observation_plan
from skill_review.engines.semantic.prompt import SYSTEM_PROMPT, build_semantic_review_prompt_payload
from skill_review.engines.semantic.response_validator import validate_semantic_response
from skill_review.engines.types import ReviewEngine, ReviewEngineContext, ReviewEngineResult
from skill_review.model.errors import SkillReviewSemanticRuntimeError
from skill_review.model.types import SemanticReviewRequest
from skill_review.runtime.semantic_context.context_builder import build_semantic_context


def _run_semantic_engine(context: ReviewEngineContext) -> ReviewEngineResult:
    if context.options.semantic_client is None:
        return ReviewEngineResult(
            engine_id="semantic",
            engine_name="AI Semantic Review",
            kind="semantic",
            coverage=[],
            findings=[],
            raw_output={
                "engine": "semantic-disabled",
                "model_name": None,
                "response": {"conclusion": "", "findings": [], "candidate_reviews": []},
                "findings": [],
                "candidate_reviews": [],
                "error": None,
            },
        )

    semantic_context = build_semantic_context(
        access=context.package_access,
        focus_findings=context.prior_findings,
    )
    prompt_payload = build_semantic_review_prompt_payload(
        review_input=context.input,
        package_summary=context.package_summary,
        semantic_context=semantic_context,
        candidate_findings=context.prior_findings,
        observations=build_semantic_observation_plan(
            behavior_facts=context.prior_artifacts.get("review_behavior_facts"),
            system_facets=context.prior_artifacts.get("skill_system_facets"),
        ),
    )
    request = SemanticReviewRequest(
        skill=context.input,
        package_summary=context.package_summary,
        candidate_findings=context.prior_findings,
        prompt_payload=prompt_payload,
        system_prompt=SYSTEM_PROMPT,
        response_json_schema=SEMANTIC_RESPONSE_JSON_SCHEMA,
    )
    response = context.options.semantic_client.review(request)
    normalized_response = normalize_semantic_candidate_review_dispositions(response.response)
    try:
        validate_semantic_response(
            response=normalized_response,
            package_paths={file.path for file in context.package_summary.files},
            required_finding_refs=collect_required_prompt_finding_refs(prompt_payload),
            prompt_payload=prompt_payload,
            response_body=response.raw_response,
        )
    except SkillReviewSemanticRuntimeError as exc:
        exc.engine = response.engine
        exc.model_name = response.model_name
        exc.token_usage = response.token_usage
        raise
    response_findings = normalized_response.get("findings") if isinstance(normalized_response, dict) else []
    semantic_findings = map_semantic_findings(response_findings)
    return ReviewEngineResult(
        engine_id="semantic",
        engine_name="AI Semantic Review",
        kind="semantic",
        coverage=[
            {
                "engine_id": "semantic",
                "engine_name": "AI Semantic Review",
                "status": "completed",
                "finding_count": len(semantic_findings),
            }
        ],
        findings=semantic_findings,
        raw_output={
            "engine": response.engine,
            "model_name": response.model_name,
            "response": normalized_response,
            "findings": [serialize_review_finding(finding) for finding in semantic_findings],
            "candidate_reviews": read_semantic_candidate_reviews(normalized_response),
            "token_usage": response.token_usage,
            "sampled_files": [
                {"path": sample.path, "size": sample.size, "truncated": sample.truncated}
                for sample in semantic_context.samples
            ],
            **({"prompt_payload": prompt_payload} if context.options.include_debug_artifacts else {}),
            "error": None,
        },
    )


def map_semantic_findings(response_findings: object) -> list[ReviewFindingDraft]:
    findings: list[ReviewFindingDraft] = []
    if not isinstance(response_findings, list):
        return findings
    for item in response_findings:
        finding = _map_semantic_finding(item)
        if finding is not None:
            findings.append(finding)
    return findings


def collect_required_prompt_finding_refs(prompt_payload: dict) -> set[str]:
    return {
        str(finding.get("finding_ref"))
        for finding in prompt_payload.get("rule_findings", [])
        if is_required_prompt_finding(finding)
    }


def is_required_prompt_finding(finding: object) -> bool:
    return isinstance(finding, dict) and finding.get("requires_candidate_review") and finding.get("finding_ref")


def _map_semantic_finding(item: object) -> ReviewFindingDraft | None:
    if not isinstance(item, dict):
        return None
    section_key = item.get("section_key")
    check_id = item.get("check_id")
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    if section_key not in {
        "auditability",
        "instruction",
        "data_asset",
        "execution",
        "authorization",
        "supply_chain",
    }:
        return None
    if not is_valid_semantic_finding_identity(check_id, title, description):
        return None
    severity = normalize_semantic_finding_severity(item)
    gate_recommendation = normalize_semantic_finding_gate_recommendation(item)
    return ReviewFindingDraft(
        source_type="agent",
        section_key=section_key,  # type: ignore[arg-type]
        check_id=check_id,
        severity=severity,
        confidence=item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium",
        category=str(item.get("category") or check_id),
        capability=str(item.get("capability") or check_id),
        title=title,
        description=description,
        recommendation=str(item.get("recommendation") or DEFAULT_SEMANTIC_RECOMMENDATION),
        gate_recommendation=gate_recommendation,
        evidence=[_build_semantic_evidence(item, description)],
    )


def is_valid_semantic_finding_identity(check_id: object, title: str, description: str) -> bool:
    return isinstance(check_id, str) and bool(check_id.strip()) and bool(title) and bool(description)


def normalize_semantic_finding_severity(item: dict) -> str:
    if has_blocking_gate_triad(item):
        return "high"
    return item.get("severity") if item.get("severity") in {"high", "medium", "low"} else "medium"


def normalize_semantic_finding_gate_recommendation(item: dict) -> str:
    if has_blocking_gate_triad(item):
        return "block"
    return (
        item.get("gate_recommendation")
        if item.get("gate_recommendation") in {"block", "attention", "info"}
        else "attention"
    )


def has_blocking_gate_triad(item: dict) -> bool:
    gate = item.get("gate")
    return (
        isinstance(gate, dict)
        and gate.get("path") == "present"
        and gate.get("impact") == "high"
        and gate.get("confirm") == "insufficient"
    )


def _build_semantic_evidence(item: dict, fallback_text: str) -> dict:
    evidence = {
        "evidence_type": "semantic",
        "text": str(item.get("semantic_evidence") or fallback_text),
    }
    related_files = item.get("related_files")
    if isinstance(related_files, list):
        evidence["related_files"] = related_files
    source_location = item.get("source_location")
    if isinstance(source_location, dict):
        evidence["location"] = source_location
    return evidence


semantic_engine = ReviewEngine(
    engine_id="semantic",
    engine_name="AI Semantic Review",
    kind="semantic",
    failure_policy="abort_review",
    run=_run_semantic_engine,
)
