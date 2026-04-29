# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Naming utility functions for parsing and normalizing identifiers."""

import re
from typing import Optional


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def normalize_name_key(value: str) -> str:
    """Normalize a name/identifier to a consistent lowercase key form.

    Converts camelCase, PascalCase, snake_case, and kebab-case to a common key form.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    # Replace camelCase boundaries with dash
    text = _CAMEL_BOUNDARY_RE.sub("-", text)
    # Replace underscores and other non-alphanumeric with dash
    text = text.replace("_", "-")
    text = _NON_ALNUM_RE.sub("-", text)
    # Collapse multiple dashes
    text = re.sub(r"-{2,}", "-", text)
    # Remove leading/trailing dashes
    text = text.strip("-")
    return text


def fuzzy_name_distance(s1: str, s2: str) -> Optional[int]:
    """Compute Levenshtein distance between two strings for fuzzy matching.

    Returns distance, or None if strings are too different to be useful matches.
    """
    s1 = str(s1 or "").lower().strip()
    s2 = str(s2 or "").lower().strip()

    if not s1 or not s2:
        return None

    # Quick check: if one is much longer than the other, not a good match
    if len(s1) > 0 and len(s2) > 0:
        max_len = max(len(s1), len(s2))
        min_len = min(len(s1), len(s2))
        if min_len == 0 or max_len / min_len > 3:
            return None

    # Compute Levenshtein distance
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,      # deletion
                dp[i][j - 1] + 1,      # insertion
                dp[i - 1][j - 1] + cost  # substitution
            )

    distance = dp[m][n]

    # Only return distance if it's reasonable (less than half the max length)
    if distance > max(len(s1), len(s2)) // 2:
        return None

    return distance


def to_pascal_case(value: str) -> str:
    """Convert a string to PascalCase.

    Handles camelCase, snake_case, kebab-case, and mixed separators.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # Replace camelCase boundaries with dash
    text = _CAMEL_BOUNDARY_RE.sub("-", text)
    # Replace underscores with dash
    text = text.replace("_", "-")
    # Replace non-alphanumeric with dash
    text = _NON_ALNUM_RE.sub("-", text)
    # Collapse multiple dashes
    text = re.sub(r"-{2,}", "-", text)
    # Split by dash and capitalize each part
    parts = [part.lower() for part in text.strip("-").split("-") if part]
    if not parts:
        return ""
    return "".join(part[:1].upper() + part[1:] for part in parts)
