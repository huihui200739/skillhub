# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_str = content[3: end_match.start() + 3]
    body = content[end_match.end() + 3:]
    parsed = _parse_frontmatter_yaml(frontmatter_str)
    if parsed is not None:
        return parsed, body
    return _parse_frontmatter_fallback(frontmatter_str), body


def _parse_frontmatter_yaml(frontmatter_str: str) -> dict[str, Any] | None:
    try:
        import yaml
    except Exception:
        return None
    try:
        parsed = yaml.safe_load(frontmatter_str) or {}
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return {}
    return {str(key).strip(): value for key, value in parsed.items() if str(key).strip()}


def _parse_frontmatter_fallback(frontmatter_str: str) -> dict[str, str]:
    frontmatter: dict[str, str] = {}
    lines = frontmatter_str.strip().split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        parsed_value = value.strip()
        if parsed_value in {"|", ">"}:
            block_lines: list[str] = []
            while index < len(lines):
                next_line = lines[index]
                if next_line and not next_line[:1].isspace() and ":" in next_line:
                    break
                block_lines.append(next_line.strip())
                index += 1
            parsed_value = "\n".join(line for line in block_lines if line).strip()
        if parsed_value.startswith('"') and parsed_value.endswith('"'):
            parsed_value = parsed_value[1:-1]
        elif parsed_value.startswith("'") and parsed_value.endswith("'"):
            parsed_value = parsed_value[1:-1]
        frontmatter[key] = parsed_value
    return frontmatter


def clean_first_paragraph(body: str, *, limit: int = 500) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    first_para = text.split("\n\n")[0]
    first_para = re.sub(r"^#+\s*", "", first_para)
    first_para = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", first_para)
    return first_para[:limit].strip()


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
