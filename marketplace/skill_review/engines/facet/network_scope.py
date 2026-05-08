from __future__ import annotations

import re

from skill_review.domain.system_facets import SkillNetworkScope


def classify_endpoint(endpoint: str) -> SkillNetworkScope:
    if re.match(r"^(https?|wss?)://(localhost|127\.|0\.0\.0\.0|\[::1\])", endpoint, re.I):
        return "loopback"
    if re.match(r"^(https?|wss?)://(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)", endpoint, re.I):
        return "private_non_loopback"
    if re.match(r"^(https?|wss?)://", endpoint, re.I):
        return "public"
    return "unknown"
