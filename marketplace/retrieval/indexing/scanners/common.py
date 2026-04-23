from __future__ import annotations

import re
from pathlib import Path


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---"):
        return {}, content

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_str = content[3: end_match.start() + 3]
    body = content[end_match.end() + 3:]
    frontmatter: dict[str, str] = {}
    for line in frontmatter_str.strip().split("\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed_value = value.strip()
        if parsed_value.startswith('"') and parsed_value.endswith('"'):
            parsed_value = parsed_value[1:-1]
        elif parsed_value.startswith("'") and parsed_value.endswith("'"):
            parsed_value = parsed_value[1:-1]
        frontmatter[key.strip()] = parsed_value
    return frontmatter, body


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
