from __future__ import annotations

import re


def build_fact_id(*, kind: str, extractor_id: str, subject: str, file: str, line: int) -> str:
    return ":".join(
        [
            normalize_token(kind),
            normalize_token(extractor_id),
            normalize_token(subject),
            normalize_token(file),
            str(line),
        ]
    )


def build_merge_key(*, kind: str, subject: str, phase: str, scope: str) -> str:
    return ":".join(
        [
            normalize_token(kind),
            normalize_token(subject),
            normalize_token(phase),
            normalize_token(scope),
        ]
    )


def normalize_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:/@+-]+", "-", str(value).strip().lower())
    return normalized.strip("-") or "unknown"
