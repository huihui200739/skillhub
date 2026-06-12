"""Skill publish-result helpers."""

from __future__ import annotations

from plugins_market.core.moderation import (
    MODERATION_APPROVED,
    MODERATION_PENDING,
    MODERATION_REJECTED,
    moderation_coalesce_display,
)

PUBLISH_RESULT_REVIEWING = "reviewing"
PUBLISH_RESULT_PENDING_MODERATION = "pending_moderation"
PUBLISH_RESULT_SUCCESS = "publish_success"
PUBLISH_RESULT_FAILED = "publish_failed"


def _nonempty(value: str | None) -> str:
    return (value or "").strip()


def coalesce_skill_publish_result(
    publish_result: str | None,
    moderation_status: str | None,
) -> str:
    """Resolve Skill publish result with legacy moderation fallback."""
    pr = _nonempty(publish_result)
    if pr:
        return pr

    ms = moderation_coalesce_display(moderation_status)
    if ms == MODERATION_PENDING:
        return PUBLISH_RESULT_PENDING_MODERATION
    if ms == MODERATION_REJECTED:
        return PUBLISH_RESULT_FAILED
    return PUBLISH_RESULT_SUCCESS


def is_skill_publish_publicly_visible(
    publish_result: str | None,
    moderation_status: str | None,
) -> bool:
    return coalesce_skill_publish_result(publish_result, moderation_status) == PUBLISH_RESULT_SUCCESS


def uses_skill_publish_visibility_model(
    publish_result: str | None,
    public_latest_version: str | None,
) -> bool:
    """Whether a skill asset has entered the publish_result/public_latest_version model."""
    return bool(_nonempty(publish_result) or _nonempty(public_latest_version))


def is_skill_asset_publicly_visible(
    *,
    publish_result: str | None,
    moderation_status: str | None,
    public_latest_version: str | None,
) -> bool:
    """
    New-model skills are public only when a public successful version exists.
    Old-model skills fall back to legacy moderation_status compatibility.
    """
    if uses_skill_publish_visibility_model(publish_result, public_latest_version):
        return bool(_nonempty(public_latest_version))
    return moderation_coalesce_display(moderation_status) == MODERATION_APPROVED


def is_skill_version_publicly_visible(
    *,
    asset_publish_result: str | None,
    asset_public_latest_version: str | None,
    version: str | None,
    version_publish_result: str | None,
    version_moderation_status: str | None,
) -> bool:
    """
    Prefer explicit version publish_result. For new-model assets with missing version-level
    state, only the public_latest_version is guaranteed public. Old-model versions fall back
    to moderation_status compatibility.
    """
    if _nonempty(version_publish_result):
        return (
            coalesce_skill_publish_result(version_publish_result, version_moderation_status) == PUBLISH_RESULT_SUCCESS
        )

    if uses_skill_publish_visibility_model(asset_publish_result, asset_public_latest_version):
        public_latest = _nonempty(asset_public_latest_version)
        current_version = _nonempty(version)
        if public_latest and current_version and public_latest == current_version:
            return True
        if _nonempty(version_moderation_status):
            return moderation_coalesce_display(version_moderation_status) == MODERATION_APPROVED
        return False

    return moderation_coalesce_display(version_moderation_status) == MODERATION_APPROVED


def is_skill_in_manual_moderation_stage(
    publish_result: str | None,
    moderation_status: str | None,
) -> bool:
    pr = coalesce_skill_publish_result(publish_result, moderation_status)
    raw_ms = (moderation_status or "").strip().upper()
    if pr == PUBLISH_RESULT_PENDING_MODERATION:
        return True
    return pr == PUBLISH_RESULT_FAILED and raw_ms == MODERATION_REJECTED


def initial_skill_publish_state(
    *,
    skill_review_enabled: bool,
    is_system_admin_publisher: bool,
) -> tuple[str, str | None]:
    if is_system_admin_publisher:
        return PUBLISH_RESULT_SUCCESS, MODERATION_APPROVED
    if skill_review_enabled:
        return PUBLISH_RESULT_REVIEWING, MODERATION_PENDING
    return PUBLISH_RESULT_PENDING_MODERATION, MODERATION_PENDING
