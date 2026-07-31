"""Diff MySQL active skills vs local Milvus index state."""

from __future__ import annotations

from dataclasses import dataclass

from recommender.offline.package_sync.db import ActiveSkillVersion

from .state import IndexState, IndexedAsset


@dataclass(frozen=True)
class IndexPlan:
    active: list[ActiveSkillVersion]
    to_upsert: list[ActiveSkillVersion]
    to_delete: list[str]  # asset_ids removed from catalog / went offline


def needs_reindex(skill: ActiveSkillVersion, indexed: IndexedAsset | None) -> bool:
    if indexed is None:
        return True
    if indexed.version != skill.latest_version:
        return True
    db_sha = (skill.artifact_sha256 or "").strip()
    if db_sha and indexed.artifact_sha256 != db_sha:
        return True
    return False


def plan_incremental(
    active_skills: list[ActiveSkillVersion],
    state: IndexState,
) -> IndexPlan:
    active_by_id = {s.asset_id: s for s in active_skills}
    active_ids = set(active_by_id)

    to_upsert = [s for s in active_skills if needs_reindex(s, state.get(s.asset_id))]
    to_delete = sorted(asset_id for asset_id in state.assets if asset_id not in active_ids)

    return IndexPlan(active=active_skills, to_upsert=to_upsert, to_delete=to_delete)


def plan_full(active_skills: list[ActiveSkillVersion]) -> IndexPlan:
    return IndexPlan(active=active_skills, to_upsert=list(active_skills), to_delete=[])
