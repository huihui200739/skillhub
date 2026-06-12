from __future__ import annotations

import re

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.facet.coverage_gap import build_read_coverage_gap_fact
from skill_review.engines.facet.extractor_shared import COMMAND_READ_MAX_BYTES, line_of
from skill_review.engines.facet.fact_builders import build_network_call_fact
from skill_review.engines.facet.network_scope import classify_endpoint
from skill_review.runtime.package_access.base import PackageAccess

DOCUMENTATION_FILE_PATTERN = re.compile(
    r"(^|/)(SKILL\.md|README(?:\.md)?|mcp-config\.json|skill\.json|package\.json|"
    r"config\.ya?ml|agents/[^/]+\.ya?ml)$",
    re.I,
)
HTTP_METHOD_ENDPOINT_PATTERN = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(https?://[^\s`\"'<>]+)", re.I)
CONFIG_URL_PATTERN = re.compile(
    r"[\"']?(?:url|endpoint|server|base_url|baseUrl)[\"']?\s*[:=]\s*[\"'](https?://[^\"']+)[\"']",
    re.I,
)
INTEGRATION_INTENT_PATTERN = re.compile(
    r"\b(api|mcp server|integration|integrations|connector|connectors|remote service|"
    r"external service|cloud backend|webhook)\b",
    re.I,
)
INTEGRATION_ACTION_PATTERN = re.compile(
    r"\b(call|connect|fetch|pull|update|create|upload|download|deploy|publish|post|get|send|search|sync)\b",
    re.I,
)
CONDITIONAL_INTEGRATION_PATTERN = re.compile(
    r"\b(if|when|available|can|could|may|optional|provided|provides|detected)\b", re.I
)
DOCUMENTED_SDK_CALL_PATTERN = re.compile(
    r"\b(openai|anthropic|requests|httpx|aiohttp|urllib\.request|huggingface_hub|boto3|slack_sdk)"
    r"\.((?:[A-Za-z_][A-Za-z0-9_]*\.)*(?:create|get|post|put|delete|patch|request|send|upload|download|list))\s*\(",
    re.I,
)


def extract_documentation_facts(
    package_access: PackageAccess,
    files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for file in [item for item in files if DOCUMENTATION_FILE_PATTERN.search(item.path)]:
        read_result = package_access.read_text_file(file.path, max_bytes=COMMAND_READ_MAX_BYTES)
        read_gap = build_read_coverage_gap_fact(
            extractor_id="documentation-capability",
            file_path=file.path,
            affects=["external_network"],
            read_result=read_result,
        )
        if read_gap:
            facts.append(read_gap)
            continue
        content = read_result.content or ""
        facts.extend(extract_documented_endpoint_facts(file.path, content))
        facts.extend(extract_documented_sdk_call_facts(file.path, content))
        facts.extend(extract_integration_intent_facts(file.path, content))
    return facts


def extract_documented_endpoint_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    method_facts = [
        build_documentation_network_fact(
            {
                "file_path": file_path,
                "line": line_of(content, match.start()),
                "sink": match.group(1).upper(),
                "endpoint": trim_endpoint(match.group(2)),
                "text": match.group(0),
                "requiredness": "required",
            }
        )
        for match in HTTP_METHOD_ENDPOINT_PATTERN.finditer(content)
        if is_actionable_endpoint_line(content, match.start())
    ]
    config_facts = [
        build_documentation_network_fact(
            {
                "file_path": file_path,
                "line": line_of(content, match.start()),
                "sink": "documented endpoint",
                "endpoint": trim_endpoint(match.group(1)),
                "text": match.group(0),
                "requiredness": "required",
            }
        )
        for match in CONFIG_URL_PATTERN.finditer(content)
        if is_config_endpoint(file_path, content, match.start())
    ]
    return [fact for fact in [*method_facts, *config_facts] if is_public_concrete_endpoint(fact["subject"])]


def extract_documented_sdk_call_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    if not is_primary_instruction_file(file_path):
        return []
    facts: list[SkillCapabilityFact] = []
    for index, line in enumerate(content.splitlines(), start=1):
        for match in DOCUMENTED_SDK_CALL_PATTERN.finditer(line):
            facts.append(
                build_documentation_network_fact(
                    {
                        "file_path": file_path,
                        "line": index,
                        "sink": f"documented sdk call: {match.group(1)}",
                        "endpoint": f"sdk:{match.group(1)}:{match.group(2)}",
                        "text": match.group(0),
                        "requiredness": "required",
                    }
                )
            )
    return facts[:4]


def extract_integration_intent_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    if not is_primary_instruction_file(file_path):
        return []
    facts: list[SkillCapabilityFact] = []
    for index, line in enumerate(content.splitlines(), start=1):
        if not is_external_integration_line(line):
            continue
        facts.append(
            build_documentation_network_fact(
                {
                    "file_path": file_path,
                    "line": index,
                    "sink": "documented external integration",
                    "endpoint": f"documented-external-integration:{normalize_subject(line)}",
                    "text": line.strip(),
                    "requiredness": resolve_integration_requiredness(line),
                }
            )
        )
    return facts[:4]


def build_documentation_network_fact(input_value: dict[str, str | int]) -> SkillCapabilityFact:
    endpoint = str(input_value["endpoint"])
    return build_network_call_fact(
        extractor_id="documentation-capability",
        source="documentation",
        confidence="high" if endpoint.startswith("http") else "medium",
        subject=endpoint,
        phase="runtime",
        file_path=str(input_value["file_path"]),
        line=int(input_value["line"]),
        text=str(input_value["text"]).strip(),
        sink=str(input_value["sink"]),
        endpoint_expression=endpoint,
        evidence_endpoint=endpoint,
        requiredness=str(input_value["requiredness"]),
        access_kind="external_service",
    )


def is_config_endpoint(file_path: str, content: str, index: int) -> bool:
    lower_path = file_path.lower()
    if lower_path.endswith(("mcp-config.json", "skill.json", "config.yaml", "config.yml")):
        return True
    if re.search(r"(^|/)agents/[^/]+\.ya?ml$", lower_path):
        return True
    return is_actionable_endpoint_line(content, index)


def is_actionable_endpoint_line(content: str, index: int) -> bool:
    line = line_at(content, index).lower()
    if re.search(
        r"(example\.com|https?://\.\.\.|license|repository|homepage|source:|xmlns|schema|documentation)", line
    ):
        return False
    return bool(
        re.search(
            r"(api|endpoint|server|session|upload|download|render|webhook|auth|token|"
            r"mcp|cloud|service|request|response|poll)",
            line,
        )
    )


def is_external_integration_line(line: str) -> bool:
    if re.search(r"\b(do not|don't|no external|not the regular)\b", line, re.I):
        return False
    return bool(INTEGRATION_INTENT_PATTERN.search(line) and INTEGRATION_ACTION_PATTERN.search(line))


def resolve_integration_requiredness(line: str) -> str:
    return "possible" if CONDITIONAL_INTEGRATION_PATTERN.search(line) else "required"


def is_primary_instruction_file(file_path: str) -> bool:
    return bool(re.search(r"(^|/)(SKILL\.md|README(?:\.md)?)$", file_path, re.I))


def is_public_concrete_endpoint(endpoint: str) -> bool:
    return classify_endpoint(endpoint) == "public" and not re.search(r"(example\.com|https?://\.\.\.)", endpoint, re.I)


def line_at(content: str, index: int) -> str:
    start = content.rfind("\n", 0, max(0, index - 1)) + 1
    end = content.find("\n", index)
    return content[start:end if end >= 0 else len(content)]


def trim_endpoint(endpoint: str) -> str:
    return re.sub(r"[),.;]+$", "", endpoint)


def normalize_subject(line: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", line.strip().lower()).strip("-")
    return normalized[:80]
