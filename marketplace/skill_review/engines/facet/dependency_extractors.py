from __future__ import annotations

import json
import re
import tomllib

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.facet.coverage_gap import build_coverage_gap_fact, build_read_coverage_gap_fact
from skill_review.engines.facet.extractor_shared import READ_MAX_BYTES
from skill_review.engines.facet.fact_builders import build_dependency_fact
from skill_review.runtime.package_access.base import PackageAccess

NODE_DEPENDENCY_FIELDS = {
    "dependencies": "runtime",
    "devDependencies": "dev",
    "peerDependencies": "build",
    "optionalDependencies": "optional",
}
REQUIREMENT_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*([<>=!~].+)?$")


def extract_node_dependency_facts(
    package_access: PackageAccess,
    files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for file in [item for item in files if item.path.endswith("package.json")]:
        read_result = package_access.read_text_file(file.path, max_bytes=READ_MAX_BYTES)
        read_gap = build_read_coverage_gap_fact(
            extractor_id="node-dependency-manifest",
            file_path=file.path,
            affects=["third_party_dependencies"],
            read_result=read_result,
        )
        if read_gap:
            facts.append(read_gap)
            continue
        facts.extend(extract_package_json_facts(file.path, read_result.content or ""))
    return facts


def extract_package_json_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return [build_parse_gap(file_path, "node-dependency-manifest", "JSON")]
    if not isinstance(parsed, dict):
        return []

    facts: list[SkillCapabilityFact] = []
    for field, scope in NODE_DEPENDENCY_FIELDS.items():
        dependencies = parsed.get(field)
        if not isinstance(dependencies, dict):
            continue
        for package_name, version_spec in dependencies.items():
            facts.append(
                build_dependency_fact(
                    extractor_id="node-dependency-manifest",
                    source="manifest",
                    confidence="high",
                    package_name=str(package_name),
                    ecosystem="npm",
                    scope=scope,
                    source_kind=classify_node_dependency_source_kind(str(version_spec)),
                    version_spec=str(version_spec),
                    text=f"{field}.{package_name}:{version_spec}",
                    file_path=file_path,
                    line=1,
                )
            )
    return facts


def extract_python_dependency_facts(
    package_access: PackageAccess,
    files: list[PackageFileEntry],
) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    for file in [item for item in files if is_python_manifest(item.path)]:
        read_result = package_access.read_text_file(file.path, max_bytes=READ_MAX_BYTES)
        read_gap = build_read_coverage_gap_fact(
            extractor_id="python-dependency-manifest",
            file_path=file.path,
            affects=["third_party_dependencies"],
            read_result=read_result,
        )
        if read_gap:
            facts.append(read_gap)
            continue
        facts.extend(extract_python_manifest_facts(file.path, read_result.content or ""))
    return facts


def extract_python_manifest_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    lower_path = file_path.lower()
    if lower_path.endswith("requirements.txt"):
        return extract_requirements_facts(file_path, content)
    if lower_path.endswith("pyproject.toml"):
        return extract_pyproject_facts(file_path, content)
    if lower_path.endswith("pipfile.lock"):
        return extract_pipfile_lock_facts(file_path, content)
    if lower_path.endswith("poetry.lock"):
        return extract_poetry_lock_facts(file_path, content)
    return []


def extract_requirements_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    facts: list[SkillCapabilityFact] = []
    registry_url: str | None = None
    for index, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        next_registry_url = parse_requirements_registry_url(line)
        if next_registry_url:
            registry_url = next_registry_url
            continue
        parsed = parse_requirement_dependency(line, registry_url)
        if parsed:
            facts.append(build_python_dependency_fact(file_path=file_path, line=index, **parsed))
    return facts


def extract_pyproject_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return [build_parse_gap(file_path, "python-dependency-manifest", "TOML")]

    facts: list[SkillCapabilityFact] = []
    project = parsed.get("project") if isinstance(parsed, dict) else None
    if isinstance(project, dict):
        facts.extend(build_pyproject_dependency_list_facts(file_path, project.get("dependencies"), "runtime"))
        facts.extend(build_project_optional_dependency_facts(file_path, project))
    poetry = parsed.get("tool", {}).get("poetry", {}) if isinstance(parsed.get("tool"), dict) else {}
    if isinstance(poetry, dict):
        facts.extend(build_poetry_dependency_table_facts(file_path, poetry.get("dependencies"), "runtime"))
        facts.extend(build_poetry_group_dependency_facts(file_path, poetry))
    return facts


def build_project_optional_dependency_facts(file_path: str, project: dict) -> list[SkillCapabilityFact]:
    optional_dependencies = project.get("optional-dependencies")
    if not isinstance(optional_dependencies, dict):
        return []
    return [
        fact
        for dependencies in optional_dependencies.values()
        for fact in build_pyproject_dependency_list_facts(file_path, dependencies, "optional")
    ]


def build_poetry_group_dependency_facts(file_path: str, poetry: dict) -> list[SkillCapabilityFact]:
    groups = poetry.get("group")
    if not isinstance(groups, dict):
        return []
    facts: list[SkillCapabilityFact] = []
    for group_name, group_value in groups.items():
        if isinstance(group_value, dict):
            scope = "dev" if str(group_name).lower() == "dev" else "runtime"
            facts.extend(build_poetry_dependency_table_facts(file_path, group_value.get("dependencies"), scope))
    return facts


def build_pyproject_dependency_list_facts(
    file_path: str,
    dependencies: object,
    scope: str,
) -> list[SkillCapabilityFact]:
    if not isinstance(dependencies, list):
        return []
    facts: list[SkillCapabilityFact] = []
    for dependency in dependencies:
        if not isinstance(dependency, str):
            continue
        parsed = parse_pyproject_dependency_string(dependency)
        if parsed:
            facts.append(build_python_dependency_fact(file_path=file_path, line=1, scope=scope, **parsed))
    return facts


def build_poetry_dependency_table_facts(
    file_path: str,
    dependencies: object,
    scope: str,
) -> list[SkillCapabilityFact]:
    if not isinstance(dependencies, dict):
        return []
    facts: list[SkillCapabilityFact] = []
    for package_name, value in dependencies.items():
        if str(package_name).lower() != "python":
            facts.append(
                build_python_dependency_fact(
                    file_path=file_path,
                    line=1,
                    package_name=str(package_name),
                    version_spec=extract_poetry_version_spec(value),
                    scope=scope,
                )
            )
    return facts


def extract_pipfile_lock_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return [build_parse_gap(file_path, "python-dependency-manifest", "JSON")]
    default_dependencies = parsed.get("default") if isinstance(parsed, dict) else None
    if not isinstance(default_dependencies, dict):
        return []
    return [
        build_python_dependency_fact(file_path=file_path, line=1, package_name=str(package_name), version_spec="")
        for package_name in default_dependencies
    ]


def extract_poetry_lock_facts(file_path: str, content: str) -> list[SkillCapabilityFact]:
    return [
        build_python_dependency_fact(
            file_path=file_path,
            line=index,
            package_name=match.group(1),
            version_spec="",
            source="lockfile",
        )
        for index, match in enumerate(re.finditer(r'^name\s*=\s*["\']([^"\']+)["\']', content, re.M), start=1)
    ]


def build_parse_gap(file_path: str, extractor_id: str, format_name: str) -> SkillCapabilityFact:
    return build_coverage_gap_fact(
        extractor_id=extractor_id,
        file_path=file_path,
        affects=["third_party_dependencies"],
        reason="parse_failed",
        detail=f"failed to parse {file_path} as {format_name}",
    )


def classify_node_dependency_source_kind(version_spec: str) -> str:
    if re.match(r"^(?:file:|link:|workspace:|\.\.?/|/)", version_spec, re.I):
        return "local_path"
    if re.match(r"^(?:git\+https?://|git\+ssh://|git://|github:|gitlab:|bitbucket:)", version_spec, re.I):
        return "git"
    if re.match(r"^https?://", version_spec, re.I):
        return "direct_url"
    return "registry_default"


def is_python_manifest(path: str) -> bool:
    return path.lower().endswith(("requirements.txt", "pyproject.toml", "poetry.lock", "pipfile.lock"))


def parse_requirements_registry_url(line: str) -> str | None:
    match = re.match(r"^(?:-i|--index-url|--extra-index-url)(?:\s+|=)(\S+)$", line)
    return match.group(1) if match else None


def parse_requirement_dependency(line: str, registry_url: str | None) -> dict[str, str] | None:
    editable = parse_editable_requirement(line, registry_url)
    if editable:
        return editable
    if line.startswith("--"):
        return None
    if is_git_requirement(line):
        return {"package_name": line, "version_spec": "", "source_kind": "git"}
    if is_direct_url_requirement(line):
        return {"package_name": line, "version_spec": "", "source_kind": "direct_url"}
    if is_local_requirement(line):
        return {"package_name": line, "version_spec": "", "source_kind": "local_path"}
    match = REQUIREMENT_PATTERN.match(line)
    if not match:
        return None
    parsed = {
        "package_name": match.group(1),
        "version_spec": match.group(2) or "",
        "source_kind": "registry_custom" if registry_url else "registry_default",
    }
    if registry_url:
        parsed["registry_url"] = registry_url
    return parsed


def parse_editable_requirement(line: str, registry_url: str | None) -> dict[str, str] | None:
    match = re.match(r"^(?:-e|--editable)(?:\s+|=)(\S+)$", line)
    if not match:
        return None
    value = match.group(1)
    source_kind = "editable_local" if is_local_requirement(value) else "registry_custom"
    if is_git_requirement(value):
        source_kind = "git"
    if is_direct_url_requirement(value):
        source_kind = "direct_url"
    parsed = {"package_name": value, "version_spec": "", "source_kind": source_kind}
    if registry_url:
        parsed["registry_url"] = registry_url
    return parsed


def build_python_dependency_fact(
    *,
    file_path: str,
    line: int,
    package_name: str,
    version_spec: str,
    source: str = "manifest",
    scope: str = "runtime",
    source_kind: str = "registry_default",
    registry_url: str | None = None,
) -> SkillCapabilityFact:
    return build_dependency_fact(
        extractor_id="python-dependency-manifest",
        source=source,
        confidence="high",
        package_name=package_name,
        ecosystem="pypi",
        scope=scope,
        source_kind=source_kind,
        version_spec=version_spec,
        registry_url=registry_url,
        text=f"{package_name}{version_spec}",
        file_path=file_path,
        line=line,
    )


def parse_pyproject_dependency_string(value: str) -> dict[str, str] | None:
    match = re.match(r"^([A-Za-z0-9_.-]+)(.*)$", value)
    if not match:
        return None
    return {"package_name": match.group(1), "version_spec": match.group(2).strip()}


def extract_poetry_version_spec(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return value["version"]
    return ""


def is_git_requirement(value: str) -> bool:
    return bool(re.match(r"^(?:git\+https?://|git\+ssh://|git://)", value, re.I))


def is_direct_url_requirement(value: str) -> bool:
    return bool(re.match(r"^https?://", value, re.I))


def is_local_requirement(value: str) -> bool:
    return bool(re.match(r"^(?:\.{1,2}/|/|file:)", value, re.I))
