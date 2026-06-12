# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from cli_core.logging_config import get_logger
from cli_core.market import PublishError, plugin_upload
from cli_core.schemas import (
    MARKETPLACE_VERSION_PATTERN,
    PluginPublishResult,
    PublishPluginInput,
    PublishRequest,
)
from cli_core.schemas.plugin import is_valid_marketplace_version
from cli_core.utils import sha256_file_hex

logger = get_logger(__name__)

PACK_IGNORE_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".eggs",
        "dist",
        "out",
        "__MACOSX",
    }
)
PACK_IGNORE_SUFFIXES = (".pyc", ".pyo", ".egg-info")
SKILL_IMPORT_BUNDLE_MAX_BYTES = 512 * 1024 * 1024

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
TOOL_NAME_PATTERN = re.compile(r'@tool\([^)]*name\s*=\s*["\']([a-z][a-z0-9-]*)["\']', re.DOTALL)

TOOLS_SCHEMA_PATH = "schemas/tools.json"
SUPPORTED_PLUGIN_TYPES = {"tools", "mcp-stdio", "restful-api", "skill", "swarmskill"}
SKILL_LIKE_RUNTIME_TYPES = frozenset({"skill", "swarmskill"})

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX_LEN = 64
SKILL_DESC_MAX_LEN = 4096
DEFAULT_TEAMSKILL_ROLES = ("id_01", "id_02")


def _collect_teamskill_role_errors(fm: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    roles = fm.get("roles", [])
    if not isinstance(roles, list) or not roles:
        errors.append("frontmatter `roles` must be a non-empty list")
        return errors

    role_ids: list[str] = []
    for i, role in enumerate(roles):
        if not isinstance(role, dict):
            errors.append(f"roles[{i}] must be an object")
            continue
        if "id" not in role:
            errors.append(f"roles[{i}] missing required field `id`")
            continue
        rid = role.get("id")
        if not isinstance(rid, str) or not rid.strip():
            errors.append(f"roles[{i}] `id` must be a non-empty string")
        else:
            role_ids.append(rid.strip())

    if len(role_ids) < 2:
        errors.append(
            "frontmatter `roles` must list at least 2 entries with valid `id` (team-skill multi-role contract)."
        )
    elif len(role_ids) != len(set(role_ids)):
        errors.append("frontmatter `roles` must not repeat the same `id`")

    return errors


def teamskill_validate_directory(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    prefix = "Teamskill: "

    if not root.exists() or not root.is_dir():
        return ([f"{prefix}not a directory: {root}"], [])

    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        return (errors, warnings)

    fm, fm_err = _parse_skill_frontmatter(skill_md)
    if fm_err:
        return ([f"{prefix}{fm_err}"], warnings)

    if fm is None:
        errors.append("SKILL.md: missing YAML frontmatter (--- ... ---)")
    elif not isinstance(fm, dict):
        errors.append("SKILL.md: frontmatter must be a mapping")
    else:
        errors.extend(f"SKILL.md: {m}" for m in _collect_teamskill_role_errors(fm))

    return ([prefix + e for e in errors], warnings)


def _display_title(kebab: str) -> str:
    return " ".join(part.capitalize() for part in kebab.split("-") if part)


def _render_skill_md(
    name: str,
    *,
    description: str,
    kind: str | None = None,
    role_ids: list[str] | None = None,
    include_version: bool = False,
) -> str:
    display = _display_title(name)
    lines: list[str] = [
        "---",
        f"name: {name}",
        f'description: "{description}"',
    ]
    if include_version:
        lines.append('version: "0.0.1"')
    if kind:
        lines.append(f"kind: {kind}")
    if role_ids is not None:
        lines.append("roles:")
        for rid in role_ids:
            lines.append(f"  - id: {rid}")
    lines.extend(
        [
            "---",
            "",
            f"# {display}",
            "",
            "## Instructions",
            "",
            "TODO: add step-by-step guidance.",
            "",
        ]
    )
    return "\n".join(lines)


def scaffold_teamskill_skill_directory(
    skill_dir: Path,
    plugin_name: str,
    role_ids: list[str],
    *,
    kind: str,
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _render_skill_md(
            plugin_name,
            description="TODO: describe this team skill.",
            kind=kind,
            role_ids=role_ids,
        ),
        encoding="utf-8",
    )


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]
    runtime_type: str | None = None


def _validate_skill_slug(value: str, *, field: str = "name") -> str | None:
    if len(value) > SKILL_NAME_MAX_LEN:
        return f"{field} must be at most {SKILL_NAME_MAX_LEN} characters (Agent Skills rules)"
    if not SKILL_NAME_PATTERN.match(value):
        return (
            f"{field} must use lowercase letters, digits, and single hyphens between segments "
            "(no leading/trailing hyphen, no '--')"
        )
    return None


def _parse_skill_frontmatter(skill_md: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"cannot read SKILL.md: {e}"
    if not text.lstrip().startswith("---"):
        return None, "SKILL.md must start with YAML frontmatter (---)"
    rest = text.lstrip()[3:].lstrip("\n")
    end = rest.find("\n---")
    if end == -1:
        return None, "SKILL.md frontmatter not closed (missing closing ---)"
    raw_fm = rest[:end]
    try:
        fm = yaml.safe_load(raw_fm)
    except yaml.YAMLError as e:
        return None, f"invalid SKILL.md frontmatter YAML: {e}"
    if not isinstance(fm, dict):
        return None, "SKILL.md frontmatter must be a mapping"
    return fm, None


def _validate_skill_frontmatter_fields(
    fm: dict[str, Any], skill_dir_name: str, yaml_name: str
) -> list[str]:
    errors: list[str] = []
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        errors.append("SKILL.md frontmatter name is required and must be a string")
    else:
        nm = name_val.strip()
        skill_nm_err = _validate_skill_slug(nm, field="SKILL.md frontmatter name")
        if skill_nm_err:
            errors.append(skill_nm_err)
        elif nm != skill_dir_name:
            errors.append("SKILL.md frontmatter name must equal the skill directory name")
        elif nm != yaml_name:
            errors.append(
                "SKILL.md frontmatter name must equal the declared skill name "
                "(plugin.yaml name when present)"
            )

    desc_val = fm.get("description")
    if not isinstance(desc_val, str):
        errors.append("SKILL.md frontmatter description is required and must be a string")
    else:
        stripped = desc_val.strip()
        if not stripped:
            errors.append("SKILL.md frontmatter description must be non-empty")
        elif len(stripped) > SKILL_DESC_MAX_LEN:
            errors.append(
                f"SKILL.md frontmatter description must be at most {SKILL_DESC_MAX_LEN} characters"
            )
    return errors


def _find_skill_subdirectory(root: Path) -> Path | None:
    """Exactly one non-hidden child directory that contains SKILL.md."""
    found: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "SKILL.md").is_file():
            found.append(child)
    if len(found) != 1:
        return None
    return found[0]


def _find_skill_workspace(root: Path) -> tuple[Path, str] | None:
    """Skill tree: flat ``root/SKILL.md`` or one child dir with ``SKILL.md``; slug from frontmatter ``name``."""
    if (root / "SKILL.md").is_file():
        fm, fm_err = _parse_skill_frontmatter(root / "SKILL.md")
        if fm_err is not None or fm is None:
            return None
        name_val = fm.get("name")
        if not isinstance(name_val, str):
            return None
        slug = name_val.strip()
        slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
        if slug_err:
            return None
        return root, slug

    nested = _find_skill_subdirectory(root)
    if nested is None or not (nested / "SKILL.md").is_file():
        return None
    fm, fm_err = _parse_skill_frontmatter(nested / "SKILL.md")
    if fm_err is not None or fm is None:
        return None
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        return None
    slug = name_val.strip()
    slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
    if slug_err:
        return None
    # Slug-shaped directory name must match ``name``; version-style dir names may differ.
    if (
        SKILL_NAME_PATTERN.match(nested.name)
        and _validate_skill_slug(nested.name, field="skill directory name") is None
        and slug != nested.name
    ):
        return None
    return nested, slug


def _diagnose_skill_like_workspace_when_unresolved(root: Path) -> list[str]:
    """Explain malformed flat/nested skill-like layouts before generic plugin checks.

    Avoid mis-reporting generic plugin requirements (``plugin.yaml``, ``README.md``) for a broken skill bundle.
    """
    candidates: list[tuple[Path, str | None]] = []
    flat = root / "SKILL.md"
    if flat.is_file():
        candidates.append((flat, None))
    nested = _find_skill_subdirectory(root)
    if nested is not None and (nested / "SKILL.md").is_file():
        candidates.append((nested / "SKILL.md", nested.name))
    if not candidates or _find_skill_workspace(root) is not None:
        return []

    skill_md, nested_dir_name = candidates[0]
    fm, fm_err = _parse_skill_frontmatter(skill_md)
    if fm_err:
        return [fm_err]
    if fm is None:
        return ["cannot parse SKILL.md frontmatter"]
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        return ["SKILL.md frontmatter name is required and must be a string"]
    slug = name_val.strip()
    if not slug:
        return ["SKILL.md frontmatter name is required and must be non-empty"]
    slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
    if slug_err:
        return [slug_err]
    if nested_dir_name and SKILL_NAME_PATTERN.match(nested_dir_name):
        nested_dir_err = _validate_skill_slug(nested_dir_name, field="skill directory name")
        if nested_dir_err is None and slug != nested_dir_name:
            return [
                f"SKILL.md frontmatter name ({slug!r}) must equal skill directory name ({nested_dir_name!r})"
            ]
    return []


def _diagnose_nested_skill_workspace(root: Path) -> list[str]:
    nested = _find_skill_subdirectory(root)
    if nested is None or not (nested / "SKILL.md").is_file():
        return []
    fm, fm_err = _parse_skill_frontmatter(nested / "SKILL.md")
    if fm_err:
        return [fm_err]
    if fm is None:
        return ["cannot parse SKILL.md frontmatter"]
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        return ["SKILL.md frontmatter name is required and must be a string"]
    slug = name_val.strip()
    if not slug:
        return ["SKILL.md frontmatter name is required and must be non-empty"]
    slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
    if slug_err:
        return [slug_err]
    if SKILL_NAME_PATTERN.match(nested.name):
        nested_dir_err = _validate_skill_slug(nested.name, field="skill directory name")
        if nested_dir_err is None and slug != nested.name:
            return [f"SKILL.md frontmatter name ({slug!r}) must equal skill directory name ({nested.name!r})"]
    return []


def _diagnose_skill_like_layout(root: Path) -> list[str]:
    return _diagnose_skill_like_workspace_when_unresolved(root) or _diagnose_nested_skill_workspace(root)


def _diagnose_skill_like_publish_root(root: Path) -> list[str]:
    if _find_skill_workspace(root) is not None:
        return []

    skill_like_diag = _diagnose_skill_like_layout(root)
    if skill_like_diag:
        return skill_like_diag

    skill_dirs = [
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").is_file()
    ]
    if len(skill_dirs) > 1:
        return [
            "skill/swarmskill publish expects exactly one skill directory under the publish root; "
            f"found {len(skill_dirs)}: {', '.join(sorted(child.name for child in skill_dirs))}"
        ]

    return [
        "skill/swarmskill publish expects root/SKILL.md or exactly one child directory containing SKILL.md"
    ]


def _infer_skill_like_runtime(root: Path) -> tuple[str | None, Path | None]:
    """Infer ``skill`` vs ``swarmskill`` from ``SKILL.md`` ``kind``; (None, None) if not skill-like."""
    ws = _find_skill_workspace(root)
    if ws is not None:
        skill_dir, _slug = ws
        fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
        if fm_err is not None or fm is None:
            return None, None
        if str(fm.get("kind") or "").strip().lower() in {"team-skill", "swarm-skill"}:
            return "swarmskill", skill_dir
        return "skill", skill_dir
    nested = _find_skill_subdirectory(root)
    if nested is None or not (nested / "SKILL.md").is_file():
        return None, None
    fm, fm_err = _parse_skill_frontmatter(nested / "SKILL.md")
    if fm_err is not None or fm is None:
        return None, None
    if str(fm.get("kind") or "").strip().lower() in {"team-skill", "swarm-skill"}:
        return "swarmskill", nested
    return "skill", nested


def _build_skill_like_plugin_yaml_for_publish(root: Path, plugin_version: str) -> dict[str, Any]:
    """Build a synthetic plugin.yaml for skill/swarmskill publish from SKILL.md + CLI version."""
    inferred_runtime, _ = _infer_skill_like_runtime(root)
    if inferred_runtime not in SKILL_LIKE_RUNTIME_TYPES:
        raise ValueError("cannot synthesize plugin.yaml: path is not skill/swarmskill layout")

    skill_ws = _find_skill_workspace(root)
    if skill_ws is None:
        raise ValueError(
            "skill layout requires root/SKILL.md (flat) or exactly one <slug>/SKILL.md under the plugin root"
        )
    skill_dir, slug = skill_ws
    fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
    if fm_err is not None or fm is None:
        raise ValueError(fm_err or "cannot read SKILL.md frontmatter")

    desc_raw = fm.get("description")
    description = desc_raw.strip() if isinstance(desc_raw, str) and desc_raw.strip() else slug
    display_raw = fm.get("display_name")
    display_name = display_raw.strip() if isinstance(display_raw, str) and display_raw.strip() else slug

    author_raw = fm.get("author")
    author = author_raw.strip() if isinstance(author_raw, str) and author_raw.strip() else "unknown"

    tags_val = fm.get("tags")
    tags: list[str] = []
    if isinstance(tags_val, list):
        tags = [str(t).strip() for t in tags_val if isinstance(t, str) and str(t).strip()]
    elif isinstance(tags_val, str) and tags_val.strip():
        tags = [tags_val.strip()]
    if not tags:
        tags = [inferred_runtime]

    return {
        "name": slug,
        "version": plugin_version,
        "display_name": display_name,
        "description": description,
        "runtime": {"type": "skill"},
        "metadata": {
            "author": author,
            "tags": tags,
        },
    }


def _init_plugin_skill(plugin_name: str, plugin_root: Path) -> Path:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for sub in ("scripts", "references", "assets"):
        (plugin_root / sub).mkdir(parents=True, exist_ok=True)

    (plugin_root / "SKILL.md").write_text(
        _render_skill_md(
            plugin_name,
            description="TODO: describe this skill for models and users",
            include_version=False,
        ),
        encoding="utf-8",
    )

    return plugin_root


def _init_plugin_swarmskill(plugin_name: str, plugin_root: Path) -> Path:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for sub in ("scripts", "references", "assets"):
        d = plugin_root / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    scaffold_teamskill_skill_directory(
        plugin_root,
        plugin_name,
        list(DEFAULT_TEAMSKILL_ROLES),
        kind="swarm-skill",
    )

    return plugin_root


def plugin_init(plugin_name: str, base_path: Path, force: bool = False, plugin_type: str = "tools") -> Path:
    if plugin_type not in SUPPORTED_PLUGIN_TYPES:
        supported = ", ".join(sorted(SUPPORTED_PLUGIN_TYPES))
        raise ValueError(f"plugin type must be one of: {supported}")
    if plugin_type in SKILL_LIKE_RUNTIME_TYPES:
        err = _validate_skill_slug(plugin_name, field="skill name")
        if err:
            raise ValueError(err)
    elif not NAME_PATTERN.match(plugin_name):
        raise ValueError("plugin name must match ^[a-z][a-z0-9-]*$")

    plugin_root = (base_path / plugin_name).resolve()
    if plugin_root.exists() and any(plugin_root.iterdir()) and not force:
        raise FileExistsError(f"{plugin_root} already exists and is not empty. Use --force to continue.")

    if plugin_type == "skill":
        return _init_plugin_skill(plugin_name, plugin_root)
    if plugin_type == "swarmskill":
        return _init_plugin_swarmskill(plugin_name, plugin_root)

    package_name = plugin_name.replace("-", "_")
    package_dir = plugin_root / "src" / package_name
    schemas_dir = plugin_root / "schemas"

    dirs = [package_dir, schemas_dir]
    for path in dirs:
        path.mkdir(parents=True, exist_ok=True)

    (plugin_root / "README.md").write_text(
        _render_readme(plugin_name, plugin_type),
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text(
        '"""Plugin package."""\n',
        encoding="utf-8",
    )
    schema_filename = "tools.json"
    schema_content = _default_restful_tools_schema() if plugin_type == "restful-api" else _default_tools_schema()
    (schemas_dir / schema_filename).write_text(
        json.dumps(schema_content, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if plugin_type == "tools":
        (package_dir / "plugin.py").write_text(
            _render_plugin_impl(plugin_name),
            encoding="utf-8",
        )
    elif plugin_type == "mcp-stdio":
        (package_dir / "mcp_server.py").write_text(
            _render_mcp_stdio_impl(plugin_name),
            encoding="utf-8",
        )
    else:
        # restful-api
        (package_dir / "rest_api.py").write_text(
            _render_rest_api_impl(plugin_name),
            encoding="utf-8",
        )
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump(
            _default_plugin_yaml(plugin_name, plugin_type, package_name),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (plugin_root / "pyproject.toml").write_text(
        _default_pyproject_toml(plugin_name, package_name, plugin_type),
        encoding="utf-8",
    )
    return plugin_root


def plugin_validate(
    plugin_path: Path,
    *,
    require_pyproject_for_tools: bool = True,
) -> ValidationResult:
    """Validate plugin or skill-like directory.

    If ``require_pyproject_for_tools`` is False, tools may use ``dist/*.whl`` only.
    """
    errors: list[str] = []
    warnings: list[str] = []
    root = plugin_path.resolve()
    if not root.exists():
        return ValidationResult(False, [f"plugin path not found: {root}"], warnings, runtime_type=None)

    plugin_yaml_path = root / "plugin.yaml"
    plugin_data: dict[str, Any] | None = None
    if plugin_yaml_path.exists():
        plugin_data = _load_yaml(plugin_yaml_path, errors)

    runtime_type = _runtime_type(plugin_data)
    if runtime_type is None:
        inferred_rt, _skill_sub_hint = _infer_skill_like_runtime(root)
        if inferred_rt is not None:
            runtime_type = inferred_rt

    skill_like_diag = _diagnose_skill_like_layout(root)
    if skill_like_diag:
        errors.extend(skill_like_diag)

    required_entries: list[str] = []
    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        skill_ws = _find_skill_workspace(root)
        if skill_ws is None and not skill_like_diag:
            errors.append(
                "skill/swarmskill: expected either root/SKILL.md (flat bundle) or exactly one "
                "child directory containing SKILL.md"
            )
    elif not skill_like_diag:
        required_entries.extend(("plugin.yaml", "README.md"))

    if runtime_type == "tools":
        required_entries.append(TOOLS_SCHEMA_PATH)
        if require_pyproject_for_tools:
            required_entries.append("pyproject.toml")
            required_entries.append("src")
        else:
            dist_dir = root / "dist"
            if not dist_dir.is_dir() or not any(dist_dir.glob("*.whl")):
                errors.append("missing required: dist/*.whl (tools wheel bundle)")
    elif runtime_type == "restful-api":
        required_entries.append(TOOLS_SCHEMA_PATH)
        required_entries.append("src")
    elif runtime_type is not None and runtime_type not in SKILL_LIKE_RUNTIME_TYPES:
        required_entries.append("src")

    for rel in required_entries:
        if not (root / rel).exists():
            errors.append(f"missing required entry: {rel}")

    tools_schema_path = root / TOOLS_SCHEMA_PATH
    tools_schema_data: dict[str, Any] | None = None
    if runtime_type in {"tools", "restful-api"} and tools_schema_path.exists():
        tools_schema_data = _load_json(tools_schema_path, errors)

    if plugin_data is not None:
        _validate_plugin_yaml(plugin_data, root, errors, warnings)

    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        skill_ws = _find_skill_workspace(root)
        if skill_ws is None:
            if not skill_like_diag:
                errors.append(
                    "skill/swarmskill: expected either root/SKILL.md (flat bundle) or exactly one "
                    "non-hidden child directory containing SKILL.md under the plugin root "
                    "(or a version-style folder whose name is not a skill slug, "
                    "with SKILL.md and matching name: in frontmatter)"
                )
        else:
            skill_dir, slug = skill_ws
            if plugin_data is not None:
                yaml_name = plugin_data.get("name")
                if isinstance(yaml_name, str) and slug != yaml_name:
                    errors.append(
                        f"skill slug {slug!r} (from layout / SKILL.md name) must equal plugin.yaml name {yaml_name!r}"
                    )
                if isinstance(yaml_name, str):
                    yaml_skill_err = _validate_skill_slug(yaml_name, field="plugin.yaml name")
                    if yaml_skill_err:
                        errors.append(yaml_skill_err)
            else:
                slug_err = _validate_skill_slug(slug, field="skill name")
                if slug_err:
                    errors.append(slug_err)

            yaml_for_fm = (
                plugin_data.get("name")
                if plugin_data is not None and isinstance(plugin_data.get("name"), str)
                else slug
            )
            fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
            if fm_err:
                errors.append(fm_err)
            elif fm is not None and isinstance(yaml_for_fm, str):
                errors.extend(_validate_skill_frontmatter_fields(fm, slug, yaml_for_fm))

            if runtime_type == "swarmskill":
                ts_errors, ts_warnings = teamskill_validate_directory(skill_dir)
                errors.extend(ts_errors)
                warnings.extend(ts_warnings)

    if tools_schema_data is not None and runtime_type == "tools":
        validated_tools = _validate_tools_json(tools_schema_data, errors)
        _validate_tool_names_consistency(
            plugin_data,
            {"tools": [tool for _, tool in validated_tools]},
            root,
            errors,
            warnings,
        )

    if tools_schema_data is not None and runtime_type == "restful-api":
        _validate_restful_api_tools_json(tools_schema_data, errors)

    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, runtime_type=runtime_type)


def plugin_pack(plugin_path: Path, output_dir: Path | None = None) -> Path:
    """Zip a plugin directory; runs ``validate`` first and raises if validation fails."""
    root = plugin_path.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"plugin path not found or not a directory: {root}")

    result = plugin_validate(root)
    for w in result.warnings:
        logger.warning("%s", w)
    if result.errors:
        raise ValueError("plugin validation failed: " + "; ".join(result.errors))

    plugin_yaml_path = root / "plugin.yaml"
    plugin_data: dict[str, Any] | None = None
    if plugin_yaml_path.is_file():
        plugin_data = yaml.safe_load(plugin_yaml_path.read_text(encoding="utf-8"))
        if not isinstance(plugin_data, dict):
            raise ValueError("plugin.yaml must be an object")

    runtime_type = _runtime_type(plugin_data) if plugin_data is not None else None
    if runtime_type is None:
        inferred_rt, _ = _infer_skill_like_runtime(root)
        if inferred_rt is not None:
            runtime_type = inferred_rt

    if runtime_type in SKILL_LIKE_RUNTIME_TYPES and plugin_data is None:
        skill_ws = _find_skill_workspace(root)
        if skill_ws is None:
            raise ValueError(
                "skill layout requires root/SKILL.md (flat) or exactly one <slug>/SKILL.md under the plugin root"
            )
        skill_dir, slug = skill_ws
        fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
        if fm_err or fm is None:
            raise ValueError(fm_err or "cannot read SKILL.md frontmatter")
        name_val = fm.get("name")
        if not isinstance(name_val, str) or name_val.strip() != slug:
            raise ValueError("SKILL.md frontmatter name must match the resolved skill slug")
        name = slug
        version = None
    else:
        if plugin_data is None:
            raise ValueError("plugin.yaml not found")
        name = plugin_data.get("name")
        version = plugin_data.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("plugin.yaml name and version required")
        if runtime_type is None:
            raise ValueError("plugin.yaml runtime.type is missing or not supported")

    out = (output_dir or (root / "out")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    zip_name = f"{name}-{version}.zip" if version else f"{name}.zip"
    zip_path = out / zip_name
    prefix = f"{name}-{version}" if version else name

    if runtime_type == "tools":
        _pack_plugin_tools(root, name, version, prefix, zip_path)
    elif runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        _pack_plugin_skill(root, name, prefix, zip_path)
    else:
        _pack_plugin_directory(root, prefix, zip_path)

    digest = sha256_file_hex(zip_path)
    sha256_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    sha256_path.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")
    return zip_path


def _pack_plugin_tools(root: Path, name: str, version: str, prefix: str, zip_path: Path) -> None:
    """Pack tools type: build wheel first, then pack metadata and ``dist/*.whl``."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise ValueError("tools type requires pyproject.toml in plugin root")
    wheel_dir = root / "dist"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", ".", "-w", str(wheel_dir), "--no-deps"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise ValueError(f"wheel build failed: {e.stderr or e.stdout or str(e)}") from e
    whls = list(wheel_dir.glob("*.whl"))
    if not whls:
        raise ValueError("wheel build produced no .whl files in dist/")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in ("plugin.yaml", "README.md", "icon.png", "schemas/tools.json"):
            p = root / rel
            if p.is_file():
                zf.write(p, f"{prefix}/{rel}".replace("\\", "/"))
        for whl in whls:
            zf.write(whl, f"{prefix}/dist/{whl.name}".replace("\\", "/"))


def _pack_plugin_skill(root: Path, name: str, prefix: str, zip_path: Path) -> None:
    """Pack skill/swarmskill: skill tree under ``<name>/`` only (no plugin.yaml / icon.png in the zip)."""
    ws = _find_skill_workspace(root)
    if ws is not None and ws[1] == name:
        skill_dir = ws[0]
    elif (root / name / "SKILL.md").is_file():
        skill_dir = root / name
    else:
        raise ValueError(
            f"skill pack: expected {name}/SKILL.md or plugin root with flat SKILL.md (name: {name} in frontmatter)"
        )
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"skill plugin requires SKILL.md under {skill_dir}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        readme = root / "README.md"
        if readme.is_file():
            zf.write(readme, f"{prefix}/README.md".replace("\\", "/"))
        arc_prefix = f"{prefix}/{name}".replace("\\", "/")
        n_files = plugin_zip_write_directory_tree(zf, skill_dir, arcname_prefix=arc_prefix)
        if n_files == 0:
            raise ValueError(f"no files packed under skill directory: {skill_dir}")


def _pack_plugin_directory(root: Path, prefix: str, zip_path: Path) -> None:
    """mcp-stdio / restful-api: full directory pack with ignore rules."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        plugin_zip_write_directory_tree(zf, root, arcname_prefix=prefix)


def plugin_zip_write_directory_tree(
    zf: zipfile.ZipFile,
    root: Path,
    *,
    arcname_prefix: str = "",
) -> int:
    """Append files under ``root`` to ``zf``; return number of files written."""
    base = root.resolve()
    prefix = arcname_prefix.strip().replace("\\", "/").strip("/")
    n = 0
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(base)
        except ValueError:
            continue
        rel = path.relative_to(base)
        parts = rel.parts
        if any(p in PACK_IGNORE_DIR_NAMES or p.endswith(".egg-info") for p in parts):
            continue
        if path.suffix in PACK_IGNORE_SUFFIXES:
            continue
        if path.name == ".DS_Store":
            continue
        rel_posix = rel.as_posix()
        arcname = f"{prefix}/{rel_posix}" if prefix else rel_posix
        zf.write(path, arcname)
        n += 1
    return n


def _zip_write_all_files(zf: zipfile.ZipFile, root: Path, *, exclude: set[Path] | None = None) -> int:
    """Write all files under ``root`` preserving relative paths (no ignore filtering)."""
    base = root.resolve()
    excluded = {p.resolve() for p in (exclude or set())}
    n = 0
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            continue
        rel = path.relative_to(base).as_posix()
        zf.write(path, rel)
        n += 1
    return n


def _prepare_publish_zip_for_upload(
    zip_path: Path,
    *,
    workspace: Path,
    plugin_version: str | None,
    expect_skill_like: bool = False,
) -> Path:
    """Normalize publish zip: inject skill-like plugin.yaml when missing."""
    stage = (workspace / "publish-zip-stage").resolve()
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        _safe_extractall(zf, stage)

    plugin_root = _find_plugin_root_in_extracted(stage)
    validation = plugin_validate(plugin_root, require_pyproject_for_tools=False)
    runtime_type = validation.runtime_type
    if expect_skill_like:
        skill_like_errors = [
            err
            for err in validation.errors
            if "missing required entry: plugin.yaml" not in err and "missing required entry: README.md" not in err
        ]
        if skill_like_errors:
            raise PublishError(400, "plugin validation failed: " + "; ".join(skill_like_errors))
        if runtime_type not in SKILL_LIKE_RUNTIME_TYPES:
            raise PublishError(
                400,
                f"expected skill/swarmskill package, got runtime.type={runtime_type or 'unknown'}",
            )
    else:
        for w in validation.warnings:
            logger.warning("%s", w)
        if validation.errors:
            raise PublishError(400, "plugin validation failed: " + "; ".join(validation.errors))

    plugin_yaml = plugin_root / "plugin.yaml"
    plugin_yaml_data: dict[str, Any] | None = None
    if plugin_yaml.is_file():
        loaded = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            plugin_yaml_data = loaded

    if runtime_type not in SKILL_LIKE_RUNTIME_TYPES:
        return zip_path

    # Skill-like bundles are normalized to canonical layout for publish:
    # <prefix>/plugin.yaml + <prefix>/<name>/SKILL.md...
    if plugin_yaml_data is None:
        if not plugin_version:
            raise PublishError(400, "skill/swarmskill publish requires --version")
        plugin_yaml_data = _build_skill_like_plugin_yaml_for_publish(plugin_root, plugin_version)

    inferred_runtime, skill_dir = _infer_skill_like_runtime(plugin_root)
    if inferred_runtime not in SKILL_LIKE_RUNTIME_TYPES or skill_dir is None:
        raise PublishError(400, "cannot infer skill/swarmskill type from SKILL.md")

    declared_runtime = _runtime_type(plugin_yaml_data)
    if declared_runtime is not None and declared_runtime not in SKILL_LIKE_RUNTIME_TYPES:
        raise PublishError(
            400,
            f"expected skill/swarmskill plugin.yaml runtime.type, got {declared_runtime or 'unknown'}",
        )
    if inferred_runtime == 'swarmskill':
        ts_errors, _ = teamskill_validate_directory(skill_dir)
        if ts_errors:
            raise PublishError(400, '; '.join(ts_errors))

    plugin_yaml_data["runtime"] = {"type": "skill"}

    if expect_skill_like:
        for w in validation.warnings:
            logger.warning("%s", w)
        kind_hint = "team-skill" if inferred_runtime == 'swarmskill' else "skill"
        logger.info("publish normalize: inferred type from SKILL.md = %s", kind_hint)

    prefix = str(plugin_yaml_data.get("name") or skill_dir.name).strip() or skill_dir.name

    repacked = workspace / "publish-ready.zip"
    with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{prefix}/plugin.yaml",
            yaml.safe_dump(plugin_yaml_data, sort_keys=False, allow_unicode=True),
        )
        readme = plugin_root / "README.md"
        if readme.is_file():
            zf.write(readme, f"{prefix}/README.md".replace("\\", "/"))
        files_written = plugin_zip_write_directory_tree(
            zf,
            skill_dir,
            arcname_prefix=f"{prefix}/{skill_dir.name}".replace("\\", "/"),
        )
    if files_written == 0:
        raise PublishError(400, f"zip file contains no skill files after normalization: {zip_path}")
    return repacked


def plugin_pack_skill_bundle(src_dir: Path, dest_zip: Path) -> None:
    """Build collection-bundle zip (zip root = ``src_dir``) for ``skill-import`` CLI upload."""
    src = src_dir.resolve()
    if not src.is_dir():
        raise ValueError(f"not a directory: {src}")
    dest_zip = dest_zip.resolve()
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            files_written = plugin_zip_write_directory_tree(zf, src, arcname_prefix="")
    except OSError as e:
        dest_zip.unlink(missing_ok=True)
        raise ValueError(f"failed to build bundle zip: {e}") from e

    if files_written == 0:
        dest_zip.unlink(missing_ok=True)
        raise ValueError(
            "no files packed (directory empty or only ignored paths such as .git / __pycache__)"
        )

    raw_size = dest_zip.stat().st_size
    if raw_size > SKILL_IMPORT_BUNDLE_MAX_BYTES:
        dest_zip.unlink(missing_ok=True)
        raise ValueError(
            f"packed bundle size {raw_size} bytes exceeds limit {SKILL_IMPORT_BUNDLE_MAX_BYTES} "
            "(same as server MAX_FILE_SIZE / 512MB)"
        )


def _safe_extractall(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip while rejecting path traversal, absolute paths, and symlinks."""
    dest = dest.resolve()
    for member in zf.infolist():
        name = member.filename
        if not isinstance(name, str) or not name:
            raise ValueError("unsafe zip entry: empty filename")
        if "\x00" in name:
            raise ValueError(f"unsafe zip entry: contains NUL byte: {name!r}")

        # Normalize separators to POSIX style for consistent checks.
        norm = name.replace("\\", "/")

        # Reject absolute paths and Windows drive paths.
        if norm.startswith("/") or norm.startswith("\\") or re.match(r"^[A-Za-z]:", norm):
            raise ValueError(f"unsafe zip entry: absolute/drive path: {name!r}")

        # Reject path traversal before filesystem resolution.
        norm2 = posixpath.normpath(norm)
        if norm2 in (".", "..") or norm2.startswith("../") or "/../" in f"/{norm2}/":
            raise ValueError(f"unsafe zip entry: path traversal: {name!r}")

        # Reject symlinks in archive (defense-in-depth).
        is_symlink = ((member.external_attr >> 16) & stat.S_IFMT(stat.S_IFLNK)) == stat.S_IFLNK
        if is_symlink:
            raise ValueError(f"unsafe zip entry: symlink not allowed: {name!r}")

        out = (dest / norm2).resolve()
        try:
            out.relative_to(dest)
        except ValueError:
            raise ValueError(f"unsafe zip entry: {name!r}") from None
    zf.extractall(dest)


def _find_plugin_root_in_extracted(extract_root: Path) -> Path:
    """Resolve staging root: flat ``SKILL.md``, nested skill folder, or ``plugin.yaml`` parent."""
    if _find_skill_workspace(extract_root) is not None:
        return extract_root
    tops = [p for p in extract_root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(tops) == 1 and _find_skill_workspace(tops[0]) is not None:
        return tops[0]

    found = list(extract_root.rglob("plugin.yaml"))
    if len(found) > 1:
        raise ValueError(
            f"expected at most one plugin.yaml in archive, found {len(found)} under {extract_root}"
        )
    if len(found) == 1:
        return found[0].parent

    if len(tops) == 1:
        raise ValueError(
            "single top-level directory is not a skill bundle "
            "(need root/SKILL.md flat or <slug>/SKILL.md) and archive has no plugin.yaml"
        )
    raise ValueError(
        "cannot resolve plugin root: need a skill bundle (flat SKILL.md, or one top dir with <slug>/SKILL.md) "
        "or a single plugin.yaml (e.g. tools/mcp/restful-api)"
    )


def _resolve_runtime_for_install(plugin_root: Path) -> tuple[str, str, Path | None]:
    """Return ``(runtime_type, name, skill_src_dir)``; ``skill_src_dir`` is set only for skill/swarmskill."""
    plugin_yaml = plugin_root / "plugin.yaml"
    if plugin_yaml.is_file():
        data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("plugin.yaml must be an object")
        runtime_type = _runtime_type(data)
        if runtime_type is None:
            raise ValueError("plugin.yaml runtime.type is missing or not supported")
        yaml_name = data.get("name")
        if not isinstance(yaml_name, str) or not yaml_name.strip():
            raise ValueError("plugin.yaml name must be a non-empty string")
        yaml_name = yaml_name.strip()
        if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
            candidate = plugin_root / yaml_name
            if candidate.is_dir() and (candidate / "SKILL.md").is_file():
                return runtime_type, yaml_name, candidate
            ws = _find_skill_workspace(plugin_root)
            if ws is None:
                raise ValueError(
                    "skill/swarmskill install with plugin.yaml requires a sibling skill directory containing SKILL.md"
                )
            skill_dir, slug = ws
            if slug != yaml_name:
                raise ValueError(
                    f"skill slug {slug!r} (from SKILL.md) must equal plugin.yaml name {yaml_name!r}"
                )
            return runtime_type, yaml_name, skill_dir
        return runtime_type, yaml_name, None

    ws = _find_skill_workspace(plugin_root)
    if ws is not None:
        skill_dir, slug = ws
        fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
        if fm_err or fm is None:
            raise ValueError(fm_err or "cannot read SKILL.md")
        name_val = fm.get("name")
        if not isinstance(name_val, str) or name_val.strip() != slug:
            raise ValueError("SKILL.md frontmatter name must match the resolved skill slug")
        rt = "swarmskill" if str(fm.get("kind") or "").strip().lower() in {"team-skill", "swarm-skill"} else "skill"
        return rt, slug, skill_dir

    raise ValueError(
        "not a skill bundle (flat SKILL.md or <slug>/SKILL.md) and plugin.yaml is missing"
    )


def _install_skill_from_staging(
    skill_src: Path,
    dest_slug: str,
    dest_parent: Path,
    *,
    force: bool,
) -> Path:
    if not skill_src.is_dir() or not (skill_src / "SKILL.md").is_file():
        raise ValueError(f"skill bundle must be a directory containing SKILL.md: {skill_src}")
    dest = dest_parent / dest_slug
    if dest.exists():
        if not force:
            raise FileExistsError(
                f"destination already exists: {dest} (use force=True or --force to overwrite)"
            )
        shutil.rmtree(dest)
    shutil.copytree(skill_src, dest)
    return dest


TOOLS_INSTALL_MAX_WHEELS = 10
TOOLS_INSTALL_MAX_WHEEL_BYTES = 50 * 1024 * 1024


def _copy_bundle_to_output(
    plugin_root: Path,
    dest_parent: Path,
    *,
    dest_name: str,
    force: bool,
) -> Path:
    """Copy plugin root to ``dest_parent / dest_name`` (from plugin.yaml, not zip root folder name)."""
    safe_name = (dest_name or "").strip()
    is_empty_name = not safe_name
    is_reserved_name = safe_name in {".", ".."}
    has_path_separator = "/" in safe_name or "\\" in safe_name
    if is_empty_name or is_reserved_name or has_path_separator:
        raise ValueError(f"invalid install destination name: {dest_name!r}")
    bundle_dest = dest_parent / safe_name
    if bundle_dest.exists():
        if not force:
            raise FileExistsError(
                f"destination already exists: {bundle_dest} (use force=True or --force to overwrite)"
            )
        shutil.rmtree(bundle_dest)
    shutil.copytree(plugin_root, bundle_dest)
    return bundle_dest


def _pip_install_tools_wheels(bundle_dest: Path) -> None:
    """``pip install`` on ``bundle_dest/dist/*.whl`` into the current Python environment."""
    bd = bundle_dest.resolve()
    whls = sorted((bd / "dist").glob("*.whl"))
    if not whls:
        raise ValueError("tools plugin zip has no dist/*.whl")
    if len(whls) > TOOLS_INSTALL_MAX_WHEELS:
        raise ValueError(
            f"tools plugin has too many wheels ({len(whls)} > {TOOLS_INSTALL_MAX_WHEELS})"
        )
    for w in whls:
        if w.name.startswith("-"):
            raise ValueError(f"unsafe wheel filename (starts with '-'): {w.name!r}")
        wheel_size = w.stat().st_size
        if wheel_size > TOOLS_INSTALL_MAX_WHEEL_BYTES:
            raise ValueError(
                f"wheel too large: {w.name!r} ({wheel_size} bytes, "
                f"limit {TOOLS_INSTALL_MAX_WHEEL_BYTES})"
            )
    wheel_paths = [str(w) for w in whls]
    cmd: list[str] = [sys.executable, "-m", "pip", "install", "--"]
    cmd.extend(wheel_paths)
    subprocess.run(cmd, check=True)


def plugin_install(
    zip_path: Path,
    *,
    extract_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Extract zip, validate, then install by ``runtime.type``.

    Skill layout copies slug dir; tools runs pip on wheels.
    """
    zpath = zip_path.resolve()
    if not zpath.is_file():
        raise ValueError(f"zip not found: {zpath}")

    dest_parent = (extract_dir if extract_dir is not None else Path.cwd()).resolve()
    dest_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="openjiuwen_install_") as tmp:
        extract_root = Path(tmp)
        with zipfile.ZipFile(zpath, "r") as zf:
            _safe_extractall(zf, extract_root)
        plugin_root = _find_plugin_root_in_extracted(extract_root)

        result = plugin_validate(plugin_root, require_pyproject_for_tools=False)
        for w in result.warnings:
            logger.warning("%s", w)
        if result.errors:
            raise ValueError("plugin validation failed: " + "; ".join(result.errors))

        runtime_type, yaml_name, skill_src = _resolve_runtime_for_install(plugin_root)

        if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
            if skill_src is None:
                raise ValueError("internal error: skill install missing resolved skill source directory")
            return _install_skill_from_staging(skill_src, yaml_name, dest_parent, force=force)

        bundle_dest = _copy_bundle_to_output(
            plugin_root,
            dest_parent,
            dest_name=yaml_name,
            force=force,
        )

        try:
            if runtime_type == "tools":
                _pip_install_tools_wheels(bundle_dest)
                logger.info("tools: installed wheels into the current Python environment")
            elif runtime_type in ("mcp-stdio", "restful-api"):
                logger.info(
                    "%s: pip not run; install dependencies manually if required",
                    runtime_type,
                )
            else:
                raise ValueError(f"unexpected runtime type after bundle copy: {runtime_type}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"pip install failed (exit {e.returncode})") from e

        return bundle_dest


def plugin_publish(
    market_url: str,
    user_token: str | None,
    system_token: str | None,
    publish_input: PublishPluginInput,
) -> PluginPublishResult:
    """Publish: optionally ``plugin_pack`` then upload, or upload an existing zip."""
    with tempfile.TemporaryDirectory(prefix="openjiuwen_publish_") as tmp:
        out_dir = Path(tmp)
        if publish_input.zip_path is not None:
            z = publish_input.zip_path
            if not z.is_file():
                raise PublishError(400, f"zip file not found: {z}")
        else:
            root = publish_input.plugin_path
            if root is None:
                raise PublishError(400, "either plugin_path or zip_path must be provided")
            if not root.exists():
                raise PublishError(400, f"plugin path not found: {root}")
            if not root.is_dir():
                raise PublishError(400, f"plugin path must be a directory: {root}")
            if publish_input.expect_skill_like:
                skill_like_errors = _diagnose_skill_like_publish_root(root)
                if skill_like_errors:
                    raise PublishError(400, "plugin validation failed: " + "; ".join(skill_like_errors))
            z = plugin_pack(root, out_dir)

        z = _prepare_publish_zip_for_upload(
            z,
            workspace=out_dir,
            plugin_version=publish_input.plugin_version,
            expect_skill_like=publish_input.expect_skill_like,
        )

        checksum_sha256 = sha256_file_hex(z)
        req = PublishRequest(
            zip_path=z,
            checksum_sha256=checksum_sha256,
            plugin_id=publish_input.plugin_id,
            plugin_version=publish_input.plugin_version,
            version_desc=publish_input.version_desc,
            force=publish_input.force,
        )
        return plugin_upload(
            market_url,
            user_token,
            system_token,
            req,
            swarmskill=publish_input.expect_skill_like,
        )


def _load_yaml(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"failed to parse YAML {path}: {exc}")
        return None
    if not isinstance(loaded, dict):
        errors.append(f"YAML root must be object: {path}")
        return None
    return loaded


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"failed to parse JSON {path}: {exc}")
        return None
    if not isinstance(loaded, dict):
        errors.append(f"JSON root must be object: {path}")
        return None
    return loaded


def _runtime_type(plugin_data: dict[str, Any] | None) -> str | None:
    """Parse and normalize ``runtime.type`` from ``plugin.yaml``."""
    if not isinstance(plugin_data, dict):
        return None
    runtime = plugin_data.get("runtime")
    if not isinstance(runtime, dict):
        return None
    raw = runtime.get("type")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    rt = raw.strip().lower()
    if not rt:
        return None
    canonical_map = {
        "tools": "tools",
        "mcp-stdio": "mcp-stdio",
        "restful-api": "restful-api",
        "skill": "skill",
        "swarmskill": "swarmskill",
        "teamskills": "swarmskill",
    }
    return canonical_map.get(rt)


def _is_valid_requires_python(value: str) -> bool:
    """Return True if ``value`` is a valid PEP 440 specifier set."""
    s = value.strip()
    if not s:
        return False
    try:
        SpecifierSet(s)
    except InvalidSpecifier:
        return False
    return True


def _validate_plugin_yaml(
    plugin_data: dict[str, Any],
    root: Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    runtime_type = _runtime_type(plugin_data)
    expected_tools_path = TOOLS_SCHEMA_PATH
    name = plugin_data.get("name")
    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        if not isinstance(name, str) or not name.strip():
            errors.append("plugin.yaml name is required")
        else:
            skill_slug_err = _validate_skill_slug(name, field="plugin.yaml name")
            if skill_slug_err:
                errors.append(skill_slug_err)
    elif not isinstance(name, str) or not NAME_PATTERN.match(name):
        errors.append("plugin.yaml name must match ^[a-z][a-z0-9-]*$")

    version = plugin_data.get("version")
    if not isinstance(version, str) or not is_valid_marketplace_version(version):
        errors.append(
            "plugin.yaml version must be x.y.z (e.g. 1.2.3) or a git commit (7 lowercase hex digits)"
        )

    display_name = plugin_data.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        errors.append("plugin.yaml display_name must be non-empty string")

    description = plugin_data.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("plugin.yaml description must be non-empty string")

    runtime = plugin_data.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("plugin.yaml runtime must be object")
    elif runtime_type is None:
        supported = ", ".join(sorted(SUPPORTED_PLUGIN_TYPES))
        errors.append(f"runtime.type must be one of: {supported}")

    metadata = plugin_data.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("plugin.yaml metadata must be object")
    else:
        author = metadata.get("author")
        if not isinstance(author, str) or not author.strip():
            errors.append("metadata.author must be non-empty string")
        tags = metadata.get("tags")
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
            errors.append("metadata.tags must be array of non-empty strings")

    compatibility = plugin_data.get("compatibility")
    if runtime_type not in SKILL_LIKE_RUNTIME_TYPES:
        if not isinstance(compatibility, dict):
            errors.append("plugin.yaml compatibility must be object")
        else:
            if "python" not in compatibility:
                errors.append("compatibility.python is required")
            else:
                py_val = compatibility["python"]
                if not isinstance(py_val, str):
                    errors.append("compatibility.python must be a string")
                elif not _is_valid_requires_python(py_val):
                    errors.append(
                        "compatibility.python must be PEP 440 version specifiers "
                        "(e.g. '>=3.11' or '>=3.11, <3.14'), same idea as pyproject requires-python"
                    )
    else:
        pass

    if runtime_type == "tools":
        tools_schema = plugin_data.get("tools_schema")
        if tools_schema is None:
            warnings.append(f"plugin.yaml tools_schema missing, defaulting to {expected_tools_path}")
        elif not isinstance(tools_schema, str):
            errors.append("plugin.yaml tools_schema must be string path")
        elif tools_schema != expected_tools_path:
            errors.append(f"plugin.yaml tools_schema must be '{expected_tools_path}'")
    elif runtime_type == "mcp-stdio":
        mcp_data = plugin_data.get("mcp")
        if not isinstance(mcp_data, dict):
            errors.append("plugin.yaml mcp must be object for mcp-stdio type")
        else:
            if mcp_data.get("transport") != "stdio":
                errors.append("mcp.transport must be 'stdio'")
            command = mcp_data.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(x, str) and x.strip() for x in command)
            ):
                errors.append("mcp.command must be non-empty string array")
    elif runtime_type == "restful-api":
        api_data = plugin_data.get("api")
        if not isinstance(api_data, dict):
            errors.append("plugin.yaml api must be object for restful-api type")
        else:
            if not isinstance(api_data.get("base_url"), str) or not api_data.get("base_url", "").strip():
                errors.append("api.base_url must be non-empty string")
    elif runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        pass


def _validate_tools_json(tools_data: dict[str, Any], errors: list[str]) -> list[tuple[int, dict[str, Any]]]:
    tools = tools_data.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools.json tools must be non-empty array")
        return []

    seen_names: set[str] = set()
    valid_tools: list[tuple[int, dict[str, Any]]] = []
    for i, tool in enumerate(tools):
        path = f"tools[{i}]"
        if not isinstance(tool, dict):
            errors.append(f"{path} must be object")
            continue

        valid_tools.append((i, tool))
        name = tool.get("name")
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            errors.append(f"{path}.name must match ^[a-z][a-z0-9-]*$")
        elif name in seen_names:
            errors.append(f"duplicate tool name: {name}")
        else:
            seen_names.add(name)

        description = tool.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{path}.description must be non-empty string")

        for schema_key in ("input_schema", "output_schema"):
            schema_obj = tool.get(schema_key)
            if not isinstance(schema_obj, dict):
                errors.append(f"{path}.{schema_key} must be object")
                continue
            if schema_obj.get("type") != "object":
                errors.append(f"{path}.{schema_key}.type must be 'object'")

    return valid_tools


def _validate_restful_input_properties(
    path: str,
    tool_path: str,
    input_schema: dict[str, Any],
    errors: list[str],
    allowed_send_methods: set[str],
) -> None:
    properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
    for param_name, param in properties.items():
        if not isinstance(param, dict):
            errors.append(f"{path}.input_schema.properties.{param_name} must be object")
            continue
        send_method = param.get("send_method")
        if send_method is not None and (
            not isinstance(send_method, str) or send_method not in allowed_send_methods
        ):
            errors.append(
                f"{path}.input_schema.properties.{param_name}.send_method "
                f"must be one of: {', '.join(sorted(allowed_send_methods))}"
            )
        param_desc = param.get("description")
        if param_desc is not None and (not isinstance(param_desc, str) or not param_desc.strip()):
            errors.append(
                f"{path}.input_schema.properties.{param_name}.description "
                "must be non-empty string when provided"
            )
        if "{" + param_name + "}" in tool_path and send_method not in ("Path", "path"):
            errors.append(f"{path}.input_schema.properties.{param_name}.send_method must be 'Path' when used in path")
        if str(send_method or "") in ("Path", "path") and ("{" + param_name + "}" not in tool_path):
            errors.append(
                f"{path}.input_schema.properties.{param_name}.send_method "
                f"is Path but {param_name} is not in path"
            )

    required = input_schema.get("required", [])
    if not isinstance(required, list):
        errors.append(f"{path}.input_schema.required must be array when provided")
        return

    for required_name in required:
        if not isinstance(required_name, str):
            errors.append(f"{path}.input_schema.required entries must be strings")
            continue
        if required_name not in properties:
            errors.append(f"{path}.input_schema.required contains unknown property: {required_name}")


def _validate_restful_output_properties(path: str, output_schema: dict[str, Any], errors: list[str]) -> None:
    output_properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
    for param_name, param in output_properties.items():
        if not isinstance(param, dict):
            errors.append(f"{path}.output_schema.properties.{param_name} must be object")
            continue
        param_desc = param.get("description")
        if param_desc is not None and (not isinstance(param_desc, str) or not param_desc.strip()):
            errors.append(
                f"{path}.output_schema.properties.{param_name}.description "
                "must be non-empty string when provided"
            )


def _validate_restful_headers(path: str, headers: Any, errors: list[str]) -> None:
    if headers is None:
        return
    if not isinstance(headers, list):
        errors.append(f"{path}.headers must be array")
        return
    for idx, header in enumerate(headers):
        header_path = f"{path}.headers[{idx}]"
        if not isinstance(header, dict):
            errors.append(f"{header_path} must be object")
            continue
        if not isinstance(header.get("name"), str) or not str(header.get("name") or "").strip():
            errors.append(f"{header_path}.name must be non-empty string")
        if not isinstance(header.get("value"), str):
            errors.append(f"{header_path}.value must be string")


def _validate_restful_api_tools_json(restful_api_tools_data: dict[str, Any], errors: list[str]) -> None:
    validated_tools = _validate_tools_json(restful_api_tools_data, errors)
    _validate_restful_api_tools(validated_tools, errors)


def _validate_restful_api_tools(
    validated_tools: list[tuple[int, dict[str, Any]]],
    errors: list[str],
) -> None:
    allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
    allowed_send_methods = {"None", "Header", "Query", "Body", "Path"}

    for i, tool in validated_tools:
        path = f"tools[{i}]"
        tool_path = tool.get("path")
        if not isinstance(tool_path, str) or not tool_path.strip():
            errors.append(f"{path}.path must be non-empty string")
            tool_path = ""

        method = tool.get("method")
        if not isinstance(method, str) or method.upper() not in allowed_methods:
            errors.append(f"{path}.method must be one of: {', '.join(sorted(allowed_methods))}")

        input_schema = tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {}
        output_schema = tool.get("output_schema") if isinstance(tool.get("output_schema"), dict) else {}
        _validate_restful_input_properties(path, tool_path, input_schema, errors, allowed_send_methods)
        _validate_restful_output_properties(path, output_schema, errors)
        _validate_restful_headers(path, tool.get("headers"), errors)




def _validate_tool_names_consistency(
    plugin_data: dict[str, Any] | None,
    tools_data: dict[str, Any],
    root: Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    if not isinstance(plugin_data, dict):
        return
    plugin_name = plugin_data.get("name")
    if not isinstance(plugin_name, str) or not NAME_PATTERN.match(plugin_name):
        return

    package_path = root / "src" / plugin_name / "plugin.py"
    fallback_path = root / "src" / plugin_name.replace("-", "_") / "plugin.py"
    plugin_py = package_path if package_path.exists() else fallback_path
    if not plugin_py.exists():
        return

    tools = tools_data.get("tools")
    if not isinstance(tools, list):
        return

    schema_tool_names = {t["name"] for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)}

    text = plugin_py.read_text(encoding="utf-8")
    code_tool_names = set(TOOL_NAME_PATTERN.findall(text))
    if not code_tool_names:
        warnings.append(f"no @tool(name=...) found in {plugin_py.relative_to(root)}")
        return

    missing_in_schema = sorted(code_tool_names - schema_tool_names)
    missing_in_code = sorted(schema_tool_names - code_tool_names)
    if missing_in_schema:
        errors.append(f"tools missing in schemas/tools.json: {', '.join(missing_in_schema)}")
    if missing_in_code:
        errors.append(f"tools in schemas/tools.json not found in plugin.py: {', '.join(missing_in_code)}")


def _default_plugin_yaml(plugin_name: str, plugin_type: str, package_name: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": plugin_name,
        "version": "0.0.1",
        "display_name": plugin_name.replace("-", " ").title(),
        "description": "TODO: describe your plugin",
        "runtime": {
            "type": plugin_type,
        },
        "metadata": {
            "author": "TODO: your name",
            "tags": ["demo"],
        },
    }
    if plugin_type not in SKILL_LIKE_RUNTIME_TYPES:
        base["compatibility"] = {"python": ">=3.11, <3.14"}
    if plugin_type == "tools":
        base["tools_schema"] = "schemas/tools.json"
    elif plugin_type == "mcp-stdio":
        base["mcp"] = {
            "transport": "stdio",
            "command": ["python", "-m", f"{package_name}.mcp_server"],
        }
    elif plugin_type in SKILL_LIKE_RUNTIME_TYPES:
        pass
    else:
        # restful-api
        base["api"] = {
            "base_url": "TODO: your API base URL",
        }
    return base


def _default_tools_schema() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "example",
                "description": "TODO: describe your tool",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
        ],
    }


def _default_restful_tools_schema() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "example-api",
                "description": "TODO: describe your REST endpoint",
                "path": "/example",
                "method": "GET",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
        ],
    }


def _render_plugin_impl(plugin_name: str) -> str:
    return f'''"""Plugin implementation for {plugin_name}."""

from openjiuwen.core.foundation.tool import tool


@tool(
    name="example",
    description="TODO: describe your tool",
    input_params={{}},
)
def example() -> dict:
    return {{}}
'''


def _render_mcp_stdio_impl(plugin_name: str) -> str:
    return f'''"""MCP stdio server for {plugin_name} (FastMCP)."""

from fastmcp import FastMCP

mcp = FastMCP("{plugin_name}")


@mcp.tool
def greet(name: str) -> str:
    """Say hello to the given name."""
    return f"Hello, {{name}}!"


if __name__ == "__main__":
    mcp.run()
'''


def _render_rest_api_impl(plugin_name: str) -> str:
    return f'''"""REST API entry for {plugin_name} (optional placeholder)."""


'''


def _default_pyproject_toml(plugin_name: str, package_name: str, plugin_type: str) -> str:
    dependencies: list[str] = []
    if plugin_type == "mcp-stdio":
        dependencies = ["fastmcp"]

    deps_toml = ""
    if dependencies:
        deps_toml = "\ndependencies = [" + ", ".join(f"\"{d}\"" for d in dependencies) + "]\n"

    return f"""[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{plugin_name}"
version = "0.0.1"
description = "openJiuwen plugin"
readme = "README.md"
requires-python = ">=3.11"
{deps_toml}

[tool.setuptools.packages.find]
where = ["src"]
include = ["{package_name}*"]
"""


def _render_readme(plugin_name: str, plugin_type: str) -> str:
    if plugin_type in SKILL_LIKE_RUNTIME_TYPES:
        type_notes = (
            f"- `{plugin_name}/SKILL.md`: skill instructions (Agent Skills layout)\n"
            f"- `{plugin_name}/scripts|references|assets/`: optional payloads\n"
        )
    elif plugin_type == "mcp-stdio":
        type_notes = "- `src/<package>/mcp_server.py`: MCP stdio entrypoint\n"
    elif plugin_type == "restful-api":
        type_notes = "- `schemas/tools.json`: REST tool contract\n- `src/<package>/rest_api.py`: REST API entry\n"
    else:
        type_notes = "- `schemas/tools.json`: tool definitions\n- `src/<package>/plugin.py`: tool implementation\n"
    return f"""# {plugin_name}

openjiuwen plugin scaffold.

## Structure

- `plugin.yaml`: plugin metadata and compatibility
{type_notes}"""


def plugin_describe_local(plugin_path: Path) -> dict[str, Any]:
    """Read local plugin README, CHANGELOG, and name/version from ``plugin.yaml`` or skill ``SKILL.md``."""
    root = plugin_path.resolve()
    result: dict[str, Any] = {"name": None, "version": None, "readme": None, "changelog": None}
    if not root.exists() or not root.is_dir():
        return result
    plugin_yaml = root / "plugin.yaml"
    if plugin_yaml.exists():
        try:
            data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                result["name"] = data.get("name")
                result["version"] = data.get("version")
        except Exception as exc:
            logger.warning("failed to read/parse plugin.yaml: %s", exc)
    if result["name"] is None:
        ws = _find_skill_workspace(root)
        if ws is not None:
            skill_dir, slug = ws
            fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
            if fm_err is None and isinstance(fm, dict):
                nm = fm.get("name")
                if isinstance(nm, str) and nm.strip() == slug:
                    result["name"] = slug
                    ver = fm.get("version")
                    if isinstance(ver, str) and MARKETPLACE_VERSION_PATTERN.match(ver.strip()):
                        result["version"] = ver.strip()
    readme_path = root / "README.md"
    if readme_path.is_file():
        try:
            result["readme"] = readme_path.read_text(encoding="utf-8")
        except Exception:
            result["readme"] = "(read failed)"
    changelog_path = root / "CHANGELOG.md"
    if changelog_path.is_file():
        try:
            result["changelog"] = changelog_path.read_text(encoding="utf-8")
        except Exception:
            result["changelog"] = "(read failed)"
    return result