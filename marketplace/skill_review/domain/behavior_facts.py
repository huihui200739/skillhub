from __future__ import annotations

from typing import Any, Literal

ReviewBehaviorFactKind = Literal[
    "process_execution",
    "dynamic_code_execution",
    "filesystem_access",
    "environment_access",
    "network_access",
    "package_or_artifact_fetch",
    "remote_state_mutation",
    "analyzability_gap",
]
ReviewBehaviorFactSource = Literal["command", "code", "documentation", "coverage"]
ReviewBehaviorFactConfidence = Literal["high", "medium", "low"]

ReviewBehaviorSourceContext = dict[str, Any]
ReviewBehaviorFactEvidence = dict[str, Any]
ReviewBehaviorFact = dict[str, Any]
ReviewBehaviorCoverageRecord = dict[str, Any]
ReviewBehaviorInventory = dict[str, Any]
