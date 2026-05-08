from __future__ import annotations

import json
import re

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.common.network_signatures import (
    ENDPOINT_COMMAND_PATTERN_SOURCE,
    URL_TRANSFER_COMMAND_PATTERN_SOURCE,
)
from skill_review.engines.facet.coverage_gap import build_read_coverage_gap_fact
from skill_review.engines.facet.extractor_shared import COMMAND_READ_MAX_BYTES, line_of
from skill_review.engines.facet.fact_builders import build_dependency_fact, build_network_call_fact
from skill_review.engines.facet.network_scope import classify_endpoint
from skill_review.runtime.package_access.base import PackageAccess

COMMAND_FILE_PATTERN = re.compile(r"\.(md|txt|sh|bash|zsh|ps1|cmd|bat)$", re.I)
STRUCTURED_COMMAND_FILE_PATTERN = re.compile(r"\.json$", re.I)
URL_COMMAND_PATTERN = re.compile(
    rf"\b{ENDPOINT_COMMAND_PATTERN_SOURCE}\b[^\n`]*?['\"]?(https?://[^\s'\"`]+)",
    re.I,
)
INSTALL_COMMAND_PATTERN = re.compile(
    r"\b(npx|uv\s+(?:add|pip\s+install|run\s+--with)|pnpm\s+(?:dlx|add|install)|"
    r"yarn\s+(?:dlx|add|install)|npm\s+(?:install|i|add)|python\s+-m\s+pip\s+install|"
    r"pip3?\s+install)\b([^\n`;&|]*)",
    re.I,
)
REMOTE_COMMAND_PATTERN = re.compile(r"\b(gh\s+api|git\s+(?:push|pull|fetch|ls-remote)|npm\s+publish)\b", re.I)
BACKTICK_URL_OPTION_COMMAND_PATTERN = re.compile(
    r"`([^`\n]*(?:--url|--endpoint|--server)\s+['\"]?(https?://[^\s'\"`]+)[^`]*)`",
    re.I,
)
LINE_URL_OPTION_COMMAND_PATTERN = re.compile(
    r"^[ \t$>]*([^\n`]*(?:--url|--endpoint|--server)\s+['\"]?(https?://[^\s'\"`]+)[^\n`]*)$",
    re.I | re.M,
)
SHELL_URL_ASSIGNMENT_PATTERN = re.compile(
    r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=(['\"])(https?://[^'\"]+)\2",
    re.I | re.M,
)
SHELL_VARIABLE_URL_COMMAND_PATTERN = re.compile(
    rf"\b({URL_TRANSFER_COMMAND_PATTERN_SOURCE})\b[^\n]*?\$(?:{{)?([A-Z][A-Z0-9_]*)(?:}})?",
    re.I,
)
COMMAND_TOKEN_PATTERN = re.compile(r"\"([^\"]+)\"|'([^']+)'|(\S+)")
FENCE_PATTERN = re.compile(r"^```([A-Za-z0-9_-]*)")
STRUCTURED_COMMAND_BLOCK_PATTERN = re.compile(r"^```([A-Za-z0-9_-]*)[^\n]*\r?\n([\s\S]*?)^```", re.M)
SHELL_FENCE_LANGUAGES = {"", "sh", "shell", "bash", "zsh", "fish", "powershell", "ps1", "cmd", "bat", "text", "txt"}
STRUCTURED_COMMAND_FENCE_LANGUAGES = {"json", "jsonc"}
REQUIREMENT_FILE_OPTIONS = {"-r", "--requirement"}
EDITABLE_INSTALL_OPTIONS = {"-e", "--editable"}
REGISTRY_URL_OPTIONS = {"-i", "--index-url", "--extra-index-url", "--registry"}
INSTALL_OPTIONS_WITH_VALUE = {
    "-i",
    "--cache-dir",
    "--config",
    "--extra-index-url",
    "--find-links",
    "--global-folder",
    "--index-url",
    "--prefix",
    "--registry",
    "--target",
    "--trusted-host",
}
ARTIFACT_ENDPOINT_PATTERN = re.compile(
    r"\.(?:zip|tar|tgz|gz|bz2|xz|whl|deb|rpm|exe|bin|pkg|dmg|jar|sh|py|pt|gguf|onnx)(?:[?#].*)?$",
    re.I,
)


def extract_command_facts(
    package_access: PackageAccess,
    files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    command_files = [item for item in files if is_command_file(item.path)]
    for file in command_files:
        read_result = package_access.read_text_file(file.path, max_bytes=COMMAND_READ_MAX_BYTES)
        read_gap = build_read_coverage_gap_fact(
            extractor_id="command-capability",
            file_path=file.path,
            affects=["external_network", "third_party_dependencies"],
            read_result=read_result,
        )
        if read_gap:
            facts.append(read_gap)
            continue
        content = read_result.content or ""
        facts.extend(extract_command_content_facts(file.path, content))
    return facts


def extract_command_content_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    if is_structured_command_file(file_path):
        return extract_structured_command_facts(file_path, content)
    return dedupe_facts(
        [
            *extract_url_command_facts(file_path, content),
            *extract_variable_url_command_facts(file_path, content),
            *extract_url_option_command_facts(file_path, content),
            *extract_structured_command_block_facts(file_path, content),
            *extract_dynamic_install_facts(file_path, content),
            *extract_remote_command_facts(file_path, content),
        ]
    )


def extract_structured_command_facts(
    file_path: str,
    content: str,
    line_offset: int = 0,
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for command_record in parse_structured_commands(content):
        command_line = pad_command_to_original_line(
            command_record["command_line"], command_record["line"] + line_offset
        )
        facts.extend(extract_url_command_facts(file_path, command_line))
        facts.extend(extract_url_option_command_facts(file_path, command_line))
        facts.extend(extract_dynamic_install_facts(file_path, command_line))
        facts.extend(extract_remote_command_facts(file_path, command_line))
    return dedupe_facts(facts)


def extract_structured_command_block_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for block in extract_structured_command_blocks(content):
        facts.extend(extract_structured_command_facts(file_path, block["content"], block["start_line"] - 1))
    return facts


def extract_structured_command_blocks(content: str) -> list[dict[str, str | int]]:
    blocks: list[dict[str, str | int]] = []
    for match in STRUCTURED_COMMAND_BLOCK_PATTERN.finditer(content):
        language = (match.group(1) or "").lower()
        if language not in STRUCTURED_COMMAND_FENCE_LANGUAGES:
            continue
        block_start = content.find("\n", match.start()) + 1
        blocks.append({"content": match.group(2) or "", "start_line": line_of(content, block_start)})
    return blocks


def parse_structured_commands(content: str) -> list[dict[str, str | int]]:
    parsed = parse_structured_command_json(content)
    if parsed is None:
        return []
    return [
        {"command_line": command, "line": find_structured_command_line(content, command)}
        for command in collect_structured_commands(parsed)
    ]


def parse_structured_command_json(content: str) -> object | None:
    try:
        return json.loads(strip_jsonc_comments(content))
    except json.JSONDecodeError:
        return parse_json_object_fragment(content)


def parse_json_object_fragment(content: str) -> object | None:
    trimmed = content.strip()
    if not re.match(r'^"[^"]+"\s*:', trimmed):
        return None
    try:
        return json.loads(f"{{{trimmed}}}")
    except json.JSONDecodeError:
        return None


def strip_jsonc_comments(content: str) -> str:
    return re.sub(r"(?m)^\s*//.*$", "", content)


def collect_structured_commands(value: object) -> list[str]:
    if isinstance(value, list):
        return [command for item in value for command in collect_structured_commands(item)]
    if not isinstance(value, dict):
        return []
    own_command = build_structured_command_line(value)
    child_commands = [command for item in value.values() for command in collect_structured_commands(item)]
    return [own_command, *child_commands] if own_command else child_commands


def build_structured_command_line(record: dict) -> str | None:
    command = record.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    args = record.get("args")
    command_args = [arg for arg in args if isinstance(arg, str)] if isinstance(args, list) else []
    return normalize_whitespace(" ".join([command, *command_args]))


def extract_url_command_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for match in URL_COMMAND_PATTERN.finditer(content):
        if not is_command_context(content, match.start()):
            continue
        endpoint = trim_command_endpoint(match.group(1))
        facts.append(
            build_network_call_fact(
                extractor_id="command-capability",
                source="command_with_endpoint",
                confidence="high",
                subject=endpoint,
                phase="install",
                file_path=file_path,
                line=line_of(content, match.start()),
                text=normalize_whitespace(match.group(0)),
                sink=normalize_whitespace(match.group(0)).split()[0],
                endpoint_expression=endpoint,
                evidence_endpoint=endpoint,
                requiredness="required",
                access_kind=infer_command_endpoint_access_kind(match.group(0), endpoint),
            )
        )
    return facts


def extract_variable_url_command_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    variables = extract_shell_url_variables(content)
    facts: list[SkillCapabilityFact] = []
    for match in SHELL_VARIABLE_URL_COMMAND_PATTERN.finditer(content):
        if not is_command_context(content, match.start()):
            continue
        endpoint = variables.get(match.group(2))
        if endpoint:
            facts.append(
                build_endpoint_command_network_fact(
                    file_path, line_of(content, match.start()), match.group(0), endpoint
                )
            )
    return facts


def extract_url_option_command_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    commands = [
        *collect_url_option_commands(content, BACKTICK_URL_OPTION_COMMAND_PATTERN),
        *collect_url_option_commands(content, LINE_URL_OPTION_COMMAND_PATTERN),
    ]
    return [
        build_endpoint_command_network_fact(file_path, command["line"], command["command"], command["endpoint"])
        for command in commands
        if is_likely_cli_command(command["command"])
    ]


def collect_url_option_commands(content: str, pattern: re.Pattern[str]) -> list[dict[str, str | int]]:
    commands: list[dict[str, str | int]] = []
    for match in pattern.finditer(content):
        if not is_command_context(content, match.start()):
            continue
        commands.append(
            {
                "command": normalize_whitespace(match.group(1)),
                "endpoint": trim_command_endpoint(match.group(2)),
                "line": line_of(content, match.start()),
            }
        )
    return commands


def extract_dynamic_install_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for match in INSTALL_COMMAND_PATTERN.finditer(content):
        if not is_command_context(content, match.start()):
            continue
        sink = normalize_whitespace(match.group(1))
        line = line_of(content, match.start())
        for subject in parse_install_subjects(sink, match.group(2) or ""):
            facts.extend(build_dynamic_install_subject_facts(file_path, line, sink, subject))
    return facts


def build_dynamic_install_subject_facts(
    file_path: str,
    line: int,
    sink: str,
    subject: dict[str, str],
) -> list[SkillCapabilityFact]:
    evidence_text = format_install_evidence(sink, subject["package_name"])
    dependency_fact = build_dependency_fact(
        extractor_id="command-capability",
        kind="dynamic_install_command",
        source="command",
        confidence="high",
        package_name=subject["package_name"],
        merge_subject=subject["package_name"],
        ecosystem=infer_install_ecosystem(sink, subject["package_name"]),
        phase="install",
        scope="runtime",
        source_kind=subject["source_kind"],
        registry_url=subject.get("registry_url"),
        text=evidence_text,
        file_path=file_path,
        line=line,
    )
    network_fact = build_install_network_fact(file_path, line, sink, subject)
    return [dependency_fact, network_fact] if network_fact else [dependency_fact]


def build_install_network_fact(
    file_path: str,
    line: int,
    sink: str,
    subject: dict[str, str],
) -> SkillCapabilityFact | None:
    endpoint = subject.get("network_endpoint")
    if not endpoint or subject["network_access_kind"] == "unknown":
        return None
    return build_network_call_fact(
        extractor_id="command-capability",
        source="command",
        confidence="medium",
        subject=format_install_network_subject(subject),
        merge_subject=endpoint,
        phase="install",
        network_scope=subject["network_scope"],
        file_path=file_path,
        line=line,
        text=format_install_evidence(sink, subject["package_name"]),
        sink=sink,
        endpoint_expression=endpoint,
        requiredness="required",
        access_kind=subject["network_access_kind"],
    )


def extract_remote_command_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    return [
        build_command_network_fact(file_path, line_of(content, match.start()), normalize_whitespace(match.group(1)))
        for match in REMOTE_COMMAND_PATTERN.finditer(content)
        if is_command_context(content, match.start())
    ]


def build_endpoint_command_network_fact(
    file_path: str,
    line: int,
    command: str,
    endpoint: str,
) -> SkillCapabilityFact:
    return build_network_call_fact(
        extractor_id="command-capability",
        source="command_with_endpoint",
        confidence="high",
        subject=endpoint,
        phase="runtime",
        file_path=file_path,
        line=line,
        text=normalize_whitespace(command),
        sink=" ".join(normalize_whitespace(command).split()[:3]),
        endpoint_expression=endpoint,
        evidence_endpoint=endpoint,
        requiredness="required",
        access_kind=infer_command_endpoint_access_kind(command, endpoint),
    )


def build_command_network_fact(file_path: str, line: int, command: str) -> SkillCapabilityFact:
    return build_network_call_fact(
        extractor_id="command-capability",
        source="command",
        confidence="medium",
        subject=command,
        phase="runtime",
        network_scope="unknown",
        file_path=file_path,
        line=line,
        text=command,
        sink=command,
        endpoint_expression=command,
        requiredness="required",
        access_kind=infer_command_access_kind(command),
    )


def parse_install_subjects(command: str, raw_arguments: str) -> list[dict[str, str]]:
    tokens = tokenize_command_arguments(raw_arguments)
    subjects = collect_install_subjects(tokens, normalize_whitespace(command))
    if not subjects and is_project_install_command(command):
        return [classify_install_subject("project dependencies", command, find_registry_url(tokens), False)]
    return subjects


def collect_install_subjects(tokens: list[str], sink: str) -> list[dict[str, str]]:
    registry_url = find_registry_url(tokens)
    subjects: list[dict[str, str]] = []
    index = 0
    while index < len(tokens):
        token = trim_command_token(tokens[index])
        editable_subject = resolve_editable_install_subject(token, next_token(tokens, index), sink, registry_url)
        requirement_file = resolve_requirement_file(token, next_token(tokens, index))
        if editable_subject:
            subjects.append(editable_subject)
            index += 1 if "=" in token else 2
            continue
        if requirement_file:
            subjects.append(classify_install_subject(requirement_file, sink, registry_url, False))
            index += 1 if "=" in token else 2
            continue
        if is_install_option(token):
            index += 2 if option_consumes_next_token(token) else 1
            continue
        if is_install_subject(token):
            subjects.append(classify_install_subject(token, sink, registry_url, False))
        index += 1
        if sink.lower() == "npx":
            break
    return dedupe_install_subjects(subjects)[:8]


def tokenize_command_arguments(raw_arguments: str) -> list[str]:
    return [
        trim_command_token(match.group(1) or match.group(2) or match.group(3) or "")
        for match in COMMAND_TOKEN_PATTERN.finditer(raw_arguments)
        if trim_command_token(match.group(1) or match.group(2) or match.group(3) or "")
    ]


def resolve_requirement_file(token: str, next_value: str | None) -> str | None:
    if token.startswith("--requirement="):
        return trim_command_token(token.removeprefix("--requirement="))
    if token not in REQUIREMENT_FILE_OPTIONS:
        return None
    candidate = trim_command_token(next_value or "")
    return candidate if candidate and not is_install_option(candidate) else None


def resolve_editable_install_subject(
    token: str,
    next_value: str | None,
    sink: str,
    registry_url: str | None,
) -> dict[str, str] | None:
    if token.startswith("--editable="):
        return classify_install_subject(trim_command_token(token.removeprefix("--editable=")), sink, registry_url, True)
    if token not in EDITABLE_INSTALL_OPTIONS:
        return None
    candidate = trim_command_token(next_value or "")
    return (
        classify_install_subject(candidate, sink, registry_url, True)
        if candidate and not is_install_option(candidate)
        else None
    )


def classify_install_subject(
    package_name: str,
    sink: str,
    registry_url: str | None,
    editable: bool,
) -> dict[str, str]:
    if is_git_dependency(package_name):
        return build_install_subject(
            package_name, "git", extract_first_url(package_name) or package_name, "artifact_source"
        )
    if is_direct_url_dependency(package_name):
        return build_install_subject(package_name, "direct_url", package_name, "artifact_source")
    if is_local_dependency(package_name):
        return build_install_subject(package_name, "editable_local" if editable else "local_path", None, "unknown")
    endpoint = registry_url or package_registry_endpoint(infer_install_ecosystem(sink, package_name))
    source_kind = "registry_custom" if registry_url else "registry_default"
    return build_install_subject(package_name, source_kind, endpoint, "package_source")


def build_install_subject(
    package_name: str,
    source_kind: str,
    network_endpoint: str | None,
    network_access_kind: str,
) -> dict[str, str]:
    result = {
        "package_name": package_name,
        "source_kind": source_kind,
        "network_access_kind": network_access_kind,
        "network_scope": classify_install_network_scope(network_endpoint),
    }
    if network_endpoint:
        result["network_endpoint"] = network_endpoint
    if source_kind == "registry_custom" and network_endpoint:
        result["registry_url"] = network_endpoint
    return result


def find_registry_url(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        normalized = trim_command_token(token)
        inline_registry_url = resolve_inline_registry_url(normalized)
        if inline_registry_url:
            return inline_registry_url
        if normalized in REGISTRY_URL_OPTIONS and next_token(tokens, index):
            return trim_command_token(next_token(tokens, index) or "")
    return None


def resolve_inline_registry_url(token: str) -> str | None:
    for option in REGISTRY_URL_OPTIONS:
        prefix = f"{option}="
        if token.startswith(prefix):
            return trim_command_token(token.removeprefix(prefix))
    return None


def infer_install_ecosystem(command: str, package_name: str = "") -> str:
    if re.search(r"pip|python\s+-m\s+pip|uv\s+(?:add|pip\s+install|run\s+--with)", command, re.I):
        return "pypi"
    if re.search(r"npm|npx|pnpm|yarn", command, re.I) or package_name.startswith("@"):
        return "npm"
    return "unknown"


def infer_command_endpoint_access_kind(command: str, endpoint: str) -> str:
    normalized_command = normalize_whitespace(command).lower()
    if re.search(r"\bgit\s+clone\b", normalized_command):
        return "artifact_source"
    if re.search(r"\b(?:curl|wget|invoke-webrequest)\b", normalized_command):
        return "artifact_source" if is_artifact_transfer_command(normalized_command, endpoint) else "external_service"
    return infer_command_access_kind(command)


def infer_command_access_kind(command: str) -> str:
    normalized_command = normalize_whitespace(command).lower()
    if re.search(r"\b(?:npm|pnpm|yarn)\s+publish\b", normalized_command):
        return "package_source"
    if re.search(r"\bgh\s+api\b", normalized_command):
        return "external_service"
    if re.search(r"\bgit\s+(?:clone|pull|fetch|push|ls-remote)\b", normalized_command):
        return "artifact_source"
    return "external_service"


def is_artifact_transfer_command(command: str, endpoint: str) -> bool:
    return bool(
        ARTIFACT_ENDPOINT_PATTERN.search(endpoint)
        or re.search(r"\|\s*(?:bash|sh|zsh|python)\b", command)
        or re.search(r"\b(?:-o|--output|-O)\b", command)
        or re.search(r"\b(?:tar|unzip|gunzip|chmod\s+\+x)\b", command)
        or not re.search(r"\b(?:-X\s+(?:POST|PUT|PATCH|DELETE)|--data|-d|-F|--form)\b", command)
    )


def is_command_file(file_path: str) -> bool:
    return bool(COMMAND_FILE_PATTERN.search(file_path) or STRUCTURED_COMMAND_FILE_PATTERN.search(file_path))


def is_structured_command_file(file_path: str) -> bool:
    return bool(STRUCTURED_COMMAND_FILE_PATTERN.search(file_path))


def is_command_context(content: str, index: int) -> bool:
    fence_language = code_fence_language_at(content, index)
    if fence_language is None or fence_language in SHELL_FENCE_LANGUAGES:
        return True
    line = line_at(content, index)
    return is_standalone_shell_command_line(line) or is_install_hint_line(line)


def is_standalone_shell_command_line(line: str) -> bool:
    return bool(
        re.search(
            r"^(?:\s*(?:#\s*)?)?(?:cd\s+\S+\s+&&\s+)?(?:curl|wget|Invoke-WebRequest|git\s+clone|"
            r"npx|uv\s+(?:add|pip\s+install|run\s+--with)|pnpm\s+(?:dlx|add|install)|"
            r"yarn\s+(?:dlx|add|install)|npm\s+(?:install|i|add|publish)|python\s+-m\s+pip\s+install|"
            r"pip3?\s+install)\b",
            line,
            re.I,
        )
    )


def is_install_hint_line(line: str) -> bool:
    return bool(
        re.search(r"\b(?:requires?|install(?:ed)? with|run with|install dependencies)\b", line, re.I)
        and INSTALL_COMMAND_PATTERN.search(line)
    )


def code_fence_language_at(content: str, index: int) -> str | None:
    active_language: str | None = None
    for line in content[:index].splitlines():
        match = FENCE_PATTERN.match(line)
        if match:
            active_language = match.group(1).lower() if active_language is None else None
    return active_language


def line_at(content: str, index: int) -> str:
    start = content.rfind("\n", 0, max(0, index - 1)) + 1
    end = content.find("\n", index)
    return content[start:end if end >= 0 else len(content)]


def find_structured_command_line(content: str, command_line: str) -> int:
    command = command_line.split()[0] if command_line.split() else ""
    command_index = content.find(json.dumps(command)) if command else -1
    return line_of(content, command_index) if command_index >= 0 else 1


def pad_command_to_original_line(command_line: str, line: int) -> str:
    prefix = "\n" * max(0, line - 1)
    return f"{prefix}{command_line}"


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def trim_command_endpoint(endpoint: str) -> str:
    return re.sub(r"[),.;]+$", "", endpoint)


def trim_command_token(token: str) -> str:
    return re.sub(r"^[`\"']+|[`\"',)]+$", "", token)


def extract_shell_url_variables(content: str) -> dict[str, str]:
    return {match.group(1): match.group(3) for match in SHELL_URL_ASSIGNMENT_PATTERN.finditer(content)}


def is_likely_cli_command(command: str) -> bool:
    return bool(
        re.search(r"^[A-Za-z0-9_./-]+(?:\s+[A-Za-z0-9_./-]+){0,6}\s+--(?:url|endpoint|server)\s+", command, re.I)
    )


def is_project_install_command(command: str) -> bool:
    return bool(re.search(r"^(npm\s+(?:install|i)|pnpm\s+install|yarn\s+install)$", command, re.I))


def is_install_option(token: str) -> bool:
    return token.startswith("-")


def is_install_subject(token: str) -> bool:
    return bool(
        token
        and "*" not in token
        and not re.fullmatch(r"[:*]+", token)
        and not is_install_option(token)
        and not re.search(r"\s", token)
    )


def option_consumes_next_token(token: str) -> bool:
    return token in INSTALL_OPTIONS_WITH_VALUE


def next_token(tokens: list[str], index: int) -> str | None:
    return tokens[index + 1] if index + 1 < len(tokens) else None


def dedupe_install_subjects(subjects: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for subject in subjects:
        key = f"{subject['package_name']}:{subject['source_kind']}:{subject.get('registry_url', '')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(subject)
    return deduped


def is_git_dependency(package_name: str) -> bool:
    return bool(re.match(r"^(?:git\+https?://|git\+ssh://|git://|github:|gitlab:|bitbucket:)", package_name, re.I))


def is_direct_url_dependency(package_name: str) -> bool:
    return bool(re.match(r"^https?://", package_name, re.I))


def is_local_dependency(package_name: str) -> bool:
    return bool(re.match(r"^(?:\.{1,2}/|/|file:|workspace:|link:)", package_name, re.I))


def extract_first_url(value: str) -> str | None:
    match = re.search(r"https?://[^\s#]+", value, re.I)
    return match.group(0) if match else None


def package_registry_endpoint(ecosystem: str) -> str:
    return f"package-registry:{ecosystem}"


def classify_install_network_scope(endpoint: str | None) -> str:
    if not endpoint:
        return "unknown"
    return classify_endpoint(endpoint) if re.match(r"^https?://", endpoint, re.I) else "public"


def format_install_network_subject(subject: dict[str, str]) -> str:
    if subject["network_access_kind"] == "package_source":
        return f"{subject.get('network_endpoint')}:{subject['package_name']}"
    return subject.get("network_endpoint") or subject["package_name"]


def format_install_evidence(command: str, package_name: str) -> str:
    return command if package_name == "project dependencies" else f"{' '.join(command.split()[:3])} {package_name}"


def dedupe_facts(facts: list[SkillCapabilityFact]) -> list[SkillCapabilityFact]:
    seen: set[str] = set()
    deduped: list[SkillCapabilityFact] = []
    for fact in facts:
        fact_id = str(fact.get("fact_id", ""))
        if fact_id in seen:
            continue
        seen.add(fact_id)
        deduped.append(fact)
    return deduped
