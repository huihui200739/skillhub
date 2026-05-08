from __future__ import annotations

import posixpath

TEXT_EXTENSIONS = {
    ".md",
    ".mdx",
    ".txt",
    ".rst",
    ".adoc",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mjs",
    ".cjs",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".xml",
    ".html",
    ".htm",
}

SCRIPT_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
}


def normalize_archive_path(value: str) -> str:
    path = str(value or "").replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    while path.startswith("/"):
        path = path[1:]
    return path


def is_safe_archive_path(path: str) -> bool:
    normalized = normalize_archive_path(path)
    parts = normalized.split("/") if normalized else []
    return bool(parts) and all(part and part not in {".", ".."} for part in parts)


def is_text_like_file(path: str) -> bool:
    base = posixpath.basename(path).lower()
    extension = posixpath.splitext(base)[1]
    return (
        extension in TEXT_EXTENSIONS
        or is_skill_file(path)
        or is_manifest_file(path)
        or is_readme_file(path)
    )


def is_skill_file(path: str) -> bool:
    return posixpath.basename(path).lower() == "skill.md"


def is_manifest_file(path: str) -> bool:
    base = posixpath.basename(path).lower()
    return base in {
        "skill.json",
        "skill.yaml",
        "skill.yml",
        "manifest.json",
        "manifest.yaml",
        "manifest.yml",
    }


def is_readme_file(path: str) -> bool:
    return posixpath.basename(path).lower().startswith("readme")


def is_prompt_like_file(path: str) -> bool:
    prompt_tokens = ["prompt", "instruction", "system", "template", "guide"]
    return any(token in path.lower() for token in prompt_tokens)


def is_summary_anchor_file(path: str) -> bool:
    return (
        is_skill_file(path)
        or is_manifest_file(path)
        or is_readme_file(path)
        or is_prompt_like_file(path)
    )


def score_review_relevant_path(path: str) -> int:
    base = posixpath.basename(path).lower()
    extension = posixpath.splitext(base)[1]
    score = 0
    if is_skill_file(path):
        score += 110
    if is_manifest_file(path):
        score += 100
    if is_readme_file(path):
        score += 90
    if is_prompt_like_file(path):
        score += 80
    if extension in SCRIPT_EXTENSIONS:
        score += 60
    if "config" in base:
        score += 30
    return score


def compare_review_relevant_paths(left_path: str, right_path: str) -> int:
    score_delta = score_review_relevant_path(right_path) - score_review_relevant_path(left_path)
    if score_delta != 0:
        return score_delta
    if left_path < right_path:
        return -1
    if left_path > right_path:
        return 1
    return 0


def looks_text_like(content: str) -> bool:
    return "\x00" not in content[:2048]
