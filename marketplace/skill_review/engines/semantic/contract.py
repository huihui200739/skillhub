from __future__ import annotations

SEMANTIC_RESPONSE_SCHEMA = """
{"conclusion":"string","findings":[{"section_key":"auditability|instruction|data_asset|execution|authorization|supply_chain","check_id":"string","severity":"high|medium|low","confidence":"high|medium|low","category":"string","capability":"string","title":"string","description":"string","semantic_evidence":"string","recommendation":"string","gate_recommendation":"block|attention|info","gate":{"path":"present|absent|uncertain","impact":"high|not_high|uncertain","confirm":"sufficient|insufficient|uncertain"},"reasoning":{"action":"string","role":"string","path":"string","impact":"string","decision":"string"},"related_files":["string"],"source_location":{"file":"string|null optional","line":1,"end_line":2}}],"candidate_reviews":[{"finding_ref":"string","disposition":"confirmed_risk|benign_example|capability_only|needs_manual_review|invalid_or_unreviewable","final_severity":"high|medium|low|info","final_gate_recommendation":"block|attention|info","confidence":"high|medium|low","reasoning":{"action":"string","role":"string","path":"string","impact":"string","decision":"string"},"rationale":"string","semantic_evidence":"string","related_files":["string"],"source_location":{"file":"string|null optional","line":1,"end_line":2}}]}
""".strip()

SEMANTIC_RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "conclusion": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "object"}},
        "candidate_reviews": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["conclusion", "findings", "candidate_reviews"],
}

ALLOWED_SECTION_KEYS = {"auditability", "instruction", "data_asset", "execution", "authorization", "supply_chain"}
ALLOWED_GATE_RECOMMENDATIONS = {"block", "attention", "info"}
ALLOWED_CANDIDATE_REVIEW_DISPOSITIONS = {
    "confirmed_risk",
    "benign_example",
    "capability_only",
    "needs_manual_review",
    "invalid_or_unreviewable",
}
DEFAULT_SEMANTIC_RECOMMENDATION = "请根据证据修复或移除该风险行为，并补充可审计的安全边界。"
