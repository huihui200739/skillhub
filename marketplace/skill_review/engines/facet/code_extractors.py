from __future__ import annotations

import re
import sys

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.common.network_signatures import (
    JS_TS_NETWORK_SINK_PATTERN_SOURCE,
    PYTHON_NETWORK_CLIENT_PATTERN_SOURCE,
    PYTHON_NETWORK_METHOD_PATTERN_SOURCE,
)
from skill_review.engines.facet.coverage_gap import build_read_coverage_gap_fact
from skill_review.engines.facet.extractor_shared import (
    READ_MAX_BYTES,
    build_runtime_network_fact,
    extract_url_constants,
    line_of,
)
from skill_review.engines.facet.fact_builders import build_network_call_fact, build_package_import_fact
from skill_review.runtime.package_access.base import PackageAccess

JS_TS_FILE_PATTERN = re.compile(r"\.(mjs|cjs|js|jsx|ts|tsx)$", re.I)
JS_TS_IMPORT_PATTERN = re.compile(r"(?:import\s+(?:[^'\"]+\s+from\s+)?|require\s*\()\s*['\"]([^'\"]+)['\"]")
JS_TS_NETWORK_PATTERN = re.compile(rf"\b({JS_TS_NETWORK_SINK_PATTERN_SOURCE})\s*\(\s*['\"]([^'\"]+)['\"]")
JS_TS_URL_CONSTANT_PATTERN = re.compile(
    r"\b(?:const|let|var)\s+([A-Z][A-Z0-9_]*)\s*=\s*['\"]((?:https?|wss?)://[^'\"]+)['\"]"
)
JS_TS_NETWORK_VARIABLE_PATTERN = re.compile(rf"\b({JS_TS_NETWORK_SINK_PATTERN_SOURCE})\s*\(\s*([A-Z][A-Z0-9_]*)\b")
NODE_BUILTINS = {
    "assert",
    "buffer",
    "child_process",
    "crypto",
    "events",
    "fs",
    "http",
    "https",
    "net",
    "os",
    "path",
    "stream",
    "timers",
    "url",
    "util",
    "zlib",
}

PYTHON_FILE_PATTERN = re.compile(r"\.py$", re.I)
PYTHON_IMPORT_PATTERN = re.compile(r"^(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_.]+))", re.M)
PYTHON_NETWORK_PATTERN = re.compile(
    rf"\b({PYTHON_NETWORK_CLIENT_PATTERN_SOURCE})\.({PYTHON_NETWORK_METHOD_PATTERN_SOURCE})"
    r"\s*\(\s*['\"]([^'\"]+)['\"]"
)
PYTHON_URL_CONSTANT_PATTERN = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:r|f|rf|fr)?['\"]((?:https?|wss?)://[^'\"]+)['\"]",
    re.M,
)
PYTHON_NETWORK_VARIABLE_PATTERN = re.compile(
    rf"\b({PYTHON_NETWORK_CLIENT_PATTERN_SOURCE})\.({PYTHON_NETWORK_METHOD_PATTERN_SOURCE})"
    r"\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\b"
)
PYTHON_SUBPROCESS_LIST_PATTERN = re.compile(
    r"\b(?:subprocess\.(?:run|call|check_call|check_output|Popen)|Popen)\s*\(\s*\[([^\]]+)\]",
    re.S,
)
PYTHON_COMMAND_LIST_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\[")
PYTHON_FUNCTION_DEFINITION_PATTERN = re.compile(
    r"^(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:->\s*[^:]+)?\s*:"
)
PYTHON_QUOTED_ARG_PATTERN = re.compile(r"[\"']([^\"']+)[\"']")
PYTHON_STDLIB = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}


def extract_js_ts_code_facts(
    package_access: PackageAccess,
    files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for file in [item for item in files if JS_TS_FILE_PATTERN.search(item.path)]:
        read_result = package_access.read_text_file(file.path, max_bytes=READ_MAX_BYTES)
        read_gap = build_read_coverage_gap_fact(
            extractor_id="jsts-code-capability",
            file_path=file.path,
            affects=["external_network", "third_party_dependencies"],
            read_result=read_result,
        )
        if read_gap:
            facts.append(read_gap)
            continue
        content = read_result.content or ""
        facts.extend(extract_js_ts_import_facts(file.path, content))
        facts.extend(extract_js_ts_network_facts(file.path, content))
    return facts


def extract_js_ts_import_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for match in JS_TS_IMPORT_PATTERN.finditer(content):
        module_name = match.group(1)
        if not is_third_party_js_module(module_name):
            continue
        facts.append(
            build_package_import_fact(
                extractor_id="jsts-code-capability",
                package_name=canonical_js_package_name(module_name),
                module_name=module_name,
                ecosystem="npm",
                language="typescript" if file_path.endswith((".ts", ".tsx")) else "javascript",
                file_path=file_path,
                line=line_of(content, match.start()),
            )
        )
    return facts


def extract_js_ts_network_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    constants = extract_url_constants(content, JS_TS_URL_CONSTANT_PATTERN)
    facts = [
        build_runtime_network_fact(
            file_path,
            line_of(content, match.start()),
            match.group(1),
            match.group(2),
            "jsts-code-capability",
        )
        for match in JS_TS_NETWORK_PATTERN.finditer(content)
    ]
    for match in JS_TS_NETWORK_VARIABLE_PATTERN.finditer(content):
        endpoint = constants.get(match.group(2))
        if endpoint:
            facts.append(
                build_runtime_network_fact(
                    file_path,
                    line_of(content, match.start()),
                    match.group(1),
                    endpoint,
                    "jsts-code-capability",
                )
            )
    return facts


def extract_python_code_facts(
    package_access: PackageAccess,
    files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for file in [item for item in files if PYTHON_FILE_PATTERN.search(item.path)]:
        read_result = package_access.read_text_file(file.path, max_bytes=READ_MAX_BYTES)
        read_gap = build_read_coverage_gap_fact(
            extractor_id="python-code-capability",
            file_path=file.path,
            affects=["external_network", "third_party_dependencies"],
            read_result=read_result,
        )
        if read_gap:
            facts.append(read_gap)
            continue
        content = read_result.content or ""
        facts.extend(extract_python_import_facts(file.path, content))
        facts.extend(extract_python_network_facts(file.path, content))
        facts.extend(extract_python_subprocess_network_facts(file.path, content))
        facts.extend(extract_python_command_list_network_facts(file.path, content))
    return facts


def extract_python_import_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for match in PYTHON_IMPORT_PATTERN.finditer(content):
        module_name = normalize_python_module(match.group(1) or match.group(2) or "")
        if not module_name or module_name in PYTHON_STDLIB:
            continue
        facts.append(
            build_package_import_fact(
                extractor_id="python-code-capability",
                package_name=module_name,
                module_name=module_name,
                ecosystem="pypi",
                language="python",
                file_path=file_path,
                line=line_of(content, match.start()),
            )
        )
    return facts


def extract_python_network_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    constants = extract_url_constants(content, PYTHON_URL_CONSTANT_PATTERN)
    facts = [
        build_runtime_network_fact(
            file_path,
            line_of(content, match.start()),
            f"{match.group(1)}.{match.group(2)}",
            match.group(3),
            "python-code-capability",
        )
        for match in PYTHON_NETWORK_PATTERN.finditer(content)
    ]
    for match in PYTHON_NETWORK_VARIABLE_PATTERN.finditer(content):
        endpoint = constants.get(match.group(3))
        if endpoint:
            facts.append(
                build_runtime_network_fact(
                    file_path,
                    line_of(content, match.start()),
                    f"{match.group(1)}.{match.group(2)}",
                    endpoint,
                    "python-code-capability",
                )
            )
    return facts


def extract_python_subprocess_network_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for match in PYTHON_SUBPROCESS_LIST_PATTERN.finditer(content):
        command = resolve_network_command(parse_quoted_args(match.group(1)))
        if command:
            facts.append(build_command_network_fact(file_path, line_of(content, match.start()), command))
    return facts


def extract_python_command_list_network_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    lines = content.splitlines()
    execution_functions = find_command_execution_functions(content)
    for index, line in enumerate(lines):
        match = PYTHON_COMMAND_LIST_ASSIGNMENT_PATTERN.match(line)
        if not match or not is_command_list_variable(match.group(1)):
            continue
        if not is_command_list_executed(lines, index, match.group(1), execution_functions):
            continue
        command = resolve_network_command(parse_quoted_args(collect_list_literal(lines, index)))
        if command:
            facts.append(build_command_network_fact(file_path, index + 1, command))
    return facts


def build_command_network_fact(file_path: str, line: int, command: str) -> SkillCapabilityFact:
    return build_network_call_fact(
        extractor_id="python-code-capability",
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


def is_third_party_js_module(module_name: str) -> bool:
    if not module_name or module_name.startswith((".", "/", "node:")):
        return False
    return canonical_js_package_name(module_name) not in NODE_BUILTINS


def canonical_js_package_name(module_name: str) -> str:
    if module_name.startswith("@"):
        parts = module_name.split("/")
        return "/".join(parts[:2])
    return module_name.split("/")[0]


def normalize_python_module(module_name: str) -> str:
    return module_name.split(".")[0]


def parse_quoted_args(value: str) -> list[str]:
    return [match.group(1) for match in PYTHON_QUOTED_ARG_PATTERN.finditer(value)]


def resolve_network_command(args: list[str]) -> str | None:
    binary = args[0] if args else ""
    subcommand = args[1] if len(args) > 1 else ""
    if binary == "gh" and subcommand == "api":
        return "gh api"
    if binary == "git" and re.match(r"^(push|pull|fetch|ls-remote|clone)$", subcommand):
        return f"git {subcommand}"
    if binary == "npm" and subcommand == "publish":
        return "npm publish"
    if binary in {"curl", "wget"} and any(re.match(r"^https?://", arg, re.I) for arg in args):
        return binary
    return None


def is_command_list_variable(variable_name: str) -> bool:
    return bool(re.search(r"(?:cmd|command|args|argv)", variable_name, re.I))


def collect_list_literal(lines: list[str], start_index: int) -> str:
    collected: list[str] = []
    for index in range(start_index, min(len(lines), start_index + 40)):
        collected.append(lines[index])
        if "]" in lines[index]:
            break
    return "\n".join(collected)


def find_command_execution_functions(content: str) -> set[str]:
    functions = extract_python_functions(content)
    execution_functions: set[str] = set()
    changed = True
    while changed:
        changed = False
        for python_function in functions:
            if python_function["name"] in execution_functions:
                continue
            if does_function_execute_parameter(python_function, execution_functions):
                execution_functions.add(python_function["name"])
                changed = True
    return execution_functions


def extract_python_functions(content: str) -> list[dict[str, object]]:
    lines = content.splitlines()
    functions: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        match = PYTHON_FUNCTION_DEFINITION_PATTERN.match(line)
        if not match:
            continue
        functions.append(
            {
                "name": match.group(2),
                "parameters": parse_python_parameters(match.group(3)),
                "body": "\n".join(collect_indented_block(lines, index + 1, len(match.group(1)))),
            }
        )
    return functions


def collect_indented_block(lines: list[str], start_index: int, parent_indent: int) -> list[str]:
    body_lines: list[str] = []
    for index in range(start_index, len(lines)):
        line = lines[index]
        if line.strip() and indentation_of(line) <= parent_indent:
            break
        body_lines.append(line)
    return body_lines


def parse_python_parameters(parameters: str) -> list[str]:
    return [
        parameter.split("=")[0].split(":")[0].strip().lstrip("*")
        for parameter in parameters.split(",")
        if parameter.split("=")[0].split(":")[0].strip().lstrip("*")
    ]


def does_function_execute_parameter(
    python_function: dict[str, object],
    execution_functions: set[str],
) -> bool:
    body = str(python_function["body"])
    return any(
        is_parameter_passed_to_subprocess(body, parameter)
        or is_parameter_passed_to_execution_function(body, parameter, execution_functions)
        for parameter in python_function["parameters"]
        if isinstance(parameter, str)
    )


def is_command_list_executed(
    lines: list[str],
    assignment_line_index: int,
    variable_name: str,
    execution_functions: set[str],
) -> bool:
    remaining_content = "\n".join(lines[assignment_line_index + 1:])
    return is_parameter_passed_to_subprocess(
        remaining_content, variable_name
    ) or is_parameter_passed_to_execution_function(
        remaining_content,
        variable_name,
        execution_functions,
    )


def is_parameter_passed_to_subprocess(content: str, parameter_name: str) -> bool:
    escaped = re.escape(parameter_name)
    return bool(
        re.search(rf"\b(?:subprocess\.(?:run|call|check_call|check_output|Popen)|Popen)\s*\(\s*{escaped}\b", content)
    )


def is_parameter_passed_to_execution_function(
    content: str,
    parameter_name: str,
    execution_functions: set[str],
) -> bool:
    if not execution_functions:
        return False
    function_names = "|".join(re.escape(function_name) for function_name in execution_functions)
    return bool(re.search(rf"\b(?:{function_names})\s*\(\s*{re.escape(parameter_name)}\b", content))


def infer_command_access_kind(command: str) -> str:
    normalized_command = command.lower()
    if re.search(r"\b(?:npm|pnpm|yarn)\s+publish\b", normalized_command):
        return "package_source"
    if re.search(r"\bgit\s+(?:clone|pull|fetch|push|ls-remote)\b", normalized_command):
        return "artifact_source"
    return "external_service"


def indentation_of(line: str) -> int:
    return len(line) - len(line.lstrip())
