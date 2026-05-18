# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from collections import defaultdict
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
import time

from sqlalchemy import and_, asc, desc, func, or_, case, exists, select, not_
from sqlalchemy.orm import Session

from plugins_market.models.market_assets import (
    MarketAssetDB,
    MarketAssetVersionDB,
    MarketSkillReviewDB,
    PluginFetchRecordDB,
    MarketAssetInteractionDB,
)
from plugins_market.core.moderation import MODERATION_APPROVED, MODERATION_PENDING
from plugins_market.schemas.plugin import AssetCreate, AssetVersionCreate, PluginListQuery
from .base_repository import MarketBaseRepository

if TYPE_CHECKING:
    from plugins_market.core.viewer_context import ViewerContext


def skill_moderation_list_clause(viewer: "ViewerContext"):
    """Skill 列表/检索：非审核管理员时仅展示已通过；发布者本人可看到本人全部 Skill 状态。"""
    if viewer.can_see_all_skill_moderation_states:
        return None
    pt = func.lower(func.coalesce(MarketAssetDB.plugin_type, ""))
    is_skill_like = or_(pt == "skill", pt == "teamskills")
    has_publish_result = and_(
        MarketAssetDB.publish_result.isnot(None),
        MarketAssetDB.publish_result != "",
    )
    public_version_exists = and_(
        MarketAssetDB.public_latest_version.isnot(None),
        MarketAssetDB.public_latest_version != "",
    )
    new_skill_model = and_(is_skill_like, or_(has_publish_result, public_version_exists))
    # 主表 moderation_status 空值历史上视为已通过；但若仅有显式 PENDING（无任一 APPROVED）且主表未回填，应对外隐藏。
    # 「旧版已通过 + 新版本待审」时须保持公开展示（与聚合 any_approved 一致）。
    pend_ver_explicit = exists(
        select(1)
        .select_from(MarketAssetVersionDB)
        .where(
            MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
            MarketAssetVersionDB.moderation_status == MODERATION_PENDING,
        )
        .correlate(MarketAssetDB)
    )
    appr_ver_explicit = exists(
        select(1)
        .select_from(MarketAssetVersionDB)
        .where(
            MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
            MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
        )
        .correlate(MarketAssetDB)
    )
    main_null_or_empty = or_(
        MarketAssetDB.moderation_status.is_(None),
        MarketAssetDB.moderation_status == "",
    )
    legacy_skill_ok = or_(
        and_(main_null_or_empty, ~pend_ver_explicit),
        and_(main_null_or_empty, pend_ver_explicit, appr_ver_explicit),
    )
    skill_public_ok = or_(MarketAssetDB.moderation_status == MODERATION_APPROVED, legacy_skill_ok)
    public_ok = or_(
        and_(pt != "skill", pt != "teamskills"),
        and_(new_skill_model, public_version_exists),
        and_(is_skill_like, not_(new_skill_model), skill_public_ok),
    )
    uid = (viewer.user_id or "").strip()
    if uid:
        return or_(public_ok, and_(is_skill_like, MarketAssetDB.publisher_id == uid))
    return public_ok


def list_icon_version_join_expr(viewer: "ViewerContext"):
    """列表 icon 联表：作者与审核管理员用 latest；他人用对外最新已通过审版本。"""
    pt = func.lower(func.coalesce(MarketAssetDB.plugin_type, ""))
    is_skill_like = or_(pt == "skill", pt == "teamskills")
    has_publish_result = and_(
        MarketAssetDB.publish_result.isnot(None),
        MarketAssetDB.publish_result != "",
    )
    public_version_exists = and_(
        MarketAssetDB.public_latest_version.isnot(None),
        MarketAssetDB.public_latest_version != "",
    )
    new_skill_model = and_(is_skill_like, or_(has_publish_result, public_version_exists))
    if viewer.is_market_moderation_admin:
        return MarketAssetDB.latest_version
    uid = (viewer.user_id or "").strip()
    if not uid:
        return case(
            (and_(is_skill_like, public_version_exists), MarketAssetDB.public_latest_version),
            (new_skill_model, None),
            else_=func.coalesce(MarketAssetDB.public_latest_version, MarketAssetDB.latest_version),
        )
    return case(
        (MarketAssetDB.publisher_id == uid, MarketAssetDB.latest_version),
        (and_(is_skill_like, public_version_exists), MarketAssetDB.public_latest_version),
        (new_skill_model, None),
        else_=func.coalesce(MarketAssetDB.public_latest_version, MarketAssetDB.latest_version),
    )


class MarketAssetRepository(MarketBaseRepository[MarketAssetDB]):
    """Data access for market_assets."""

    def __init__(self, db: Session):
        super().__init__(db, MarketAssetDB)

    def create_asset(self, params: AssetCreate) -> MarketAssetDB:
        now_ms = int(time.time() * 1000)
        obj_in = params.model_dump()
        obj_in.update({"create_time": now_ms, "update_time": now_ms})
        return self.create(obj_in)

    def update_latest_version(self, asset: MarketAssetDB, version: str) -> MarketAssetDB:
        now_ms = int(time.time() * 1000)
        return self.update(
            asset,
            {
                "latest_version": version,
                "update_time": now_ms,
            },
        )

    def get_by_asset_id(self, asset_id: str) -> Optional[MarketAssetDB]:
        return self.filter_by(asset_id=asset_id).first()

    def list_by_publisher_name_and_type(
        self,
        publisher_id: str,
        name: str,
        asset_type: str = "plugin",
    ) -> List[MarketAssetDB]:
        """All assets for same publisher + exact name + type (0/1/many for publish resolution)."""
        return self.filter_by(
            publisher_id=publisher_id,
            name=name,
            asset_type=asset_type,
        ).all()

    def list_by_publisher(
        self,
        publisher_id: str,
        limit: int = 50,
    ) -> List[MarketAssetDB]:
        return (
            self.filter_by(publisher_id=publisher_id)
            .order_by(MarketAssetDB.create_time.desc())
            .limit(limit)
            .all()
        )

    def count_skills_by_publisher(self, publisher_id: str) -> int:
        """Count published skill-like assets by publisher (excludes OFFLINE status)."""
        return (
            self.query()
            .filter(
                MarketAssetDB.publisher_id == publisher_id,
                MarketAssetDB.plugin_type.in_(("skill", "teamskills")),
                MarketAssetDB.status != "OFFLINE",
            )
            .count()
        )

    def get_by_external_id(self, external_id: str) -> MarketAssetDB | None:
        eid = (external_id or "").strip()
        if not eid:
            return None
        return self.query().filter(MarketAssetDB.external_id == eid).first()

    def search_by_name(
        self,
        keyword: str,
        limit: int = 50,
    ) -> List[MarketAssetDB]:
        return (
            self.query()
            .filter(MarketAssetDB.name.ilike(f"%{keyword}%"))
            .order_by(MarketAssetDB.view_count.desc())
            .limit(limit)
            .all()
        )

    def list_plugins(
        self,
        params: PluginListQuery,
        *,
        viewer: "ViewerContext",
    ) -> Tuple[List[Tuple[MarketAssetDB, Optional[str], bool]], int]:
        """
        分页查询插件列表，默认排除 status=OFFLINE 的资源。
        支持按 asset_id、asset_type、publisher_id、publisher_name（模糊）、
        category_id（精确匹配）、
        plugin_type（精确匹配）、
        search_keyword（对 name/display_name/short_desc/detail_desc 做 OR 模糊）过滤，
        按 order_by 排序。
        返回每行 (asset, file_path, has_icon)。
        """
        q_assets = self.query().filter(MarketAssetDB.status != "OFFLINE")

        if params.asset_id:
            q_assets = q_assets.filter(MarketAssetDB.asset_id == params.asset_id)
        if params.asset_type:
            q_assets = q_assets.filter(MarketAssetDB.asset_type == params.asset_type)
        if params.publisher_id:
            q_assets = q_assets.filter(MarketAssetDB.publisher_id == params.publisher_id)
        if params.publisher_name and params.publisher_name.strip():
            q_assets = q_assets.filter(
                MarketAssetDB.publisher_name.ilike(f"%{params.publisher_name.strip()}%")
            )
        if params.category_id and params.category_id.strip():
            q_assets = q_assets.filter(MarketAssetDB.category_id == params.category_id.strip())
        if params.plugin_type and params.plugin_type.strip():
            q_assets = q_assets.filter(MarketAssetDB.plugin_type == params.plugin_type.strip())
        if params.plugin_type_exclude and params.plugin_type_exclude.strip():
            ex = params.plugin_type_exclude.strip()
            q_assets = q_assets.filter(
                or_(
                    MarketAssetDB.plugin_type.is_(None),
                    MarketAssetDB.plugin_type != ex,
                )
            )
        if params.search_keyword and params.search_keyword.strip():
            kw = f"%{params.search_keyword.strip()}%"
            q_assets = q_assets.filter(
                or_(
                    MarketAssetDB.name.ilike(kw),
                    MarketAssetDB.display_name.ilike(kw),
                    MarketAssetDB.short_desc.ilike(kw),
                    MarketAssetDB.detail_desc.ilike(kw),
                )
            )

        mod_clause = skill_moderation_list_clause(viewer)
        if mod_clause is not None:
            q_assets = q_assets.filter(mod_clause)

        ms = (params.moderation_status or "").strip().upper() if params.moderation_status else ""
        if ms == "PENDING":
            # Skill / teamskills：系统审查通过后等待人工审核，或旧数据显式 PENDING，均视为待审。
            pt = (params.plugin_type or "").strip().lower()
            if pt in ("skill", "teamskills") or not (params.plugin_type or "").strip():
                # 显式 FROM + 仅与外层 market_assets 相关，避免与外层 join 的 version 表 auto-correlate 掉内层 FROM
                pend_subq = (
                    select(1)
                    .select_from(MarketAssetVersionDB)
                    .where(
                        MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                        or_(
                            MarketAssetVersionDB.publish_result == "pending_moderation",
                            and_(
                                or_(
                                    MarketAssetVersionDB.publish_result.is_(None),
                                    MarketAssetVersionDB.publish_result == "",
                                ),
                                MarketAssetVersionDB.moderation_status == "PENDING",
                            ),
                        ),
                    )
                    .correlate(MarketAssetDB)
                )
                pend_by_version = exists(pend_subq)
                q_assets = q_assets.filter(pend_by_version)
            else:
                q_assets = q_assets.filter(MarketAssetDB.moderation_status == "PENDING")
        elif ms == "REJECTED":
            q_assets = q_assets.filter(MarketAssetDB.moderation_status == "REJECTED")
        elif ms == "APPROVED":
            q_assets = q_assets.filter(
                or_(
                    MarketAssetDB.moderation_status.is_(None),
                    MarketAssetDB.moderation_status == "",
                    MarketAssetDB.moderation_status == "APPROVED",
                )
            )

        total = q_assets.count()

        order_col = getattr(
            MarketAssetDB,
            params.order_by if hasattr(MarketAssetDB, params.order_by) else "install_count",
        )
        # 置顶：pin_order 非 NULL 的排在前，且 pin_order 升序；其余按原 order_by 规则
        pin_group = case((MarketAssetDB.pin_order.is_(None), 1), else_=0)
        q_assets = q_assets.order_by(
            asc(pin_group),
            asc(MarketAssetDB.pin_order),
            desc(order_col) if params.desc else asc(order_col),
            asc(MarketAssetDB.asset_id),
        )

        page = max(1, params.page)
        page_size = max(1, min(params.page_size, 200))
        offset = (page - 1) * page_size
        q = (
            q_assets.outerjoin(
                MarketAssetVersionDB,
                and_(
                    MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                    MarketAssetVersionDB.version == list_icon_version_join_expr(viewer),
                ),
            )
            .add_columns(MarketAssetVersionDB.file_path, MarketAssetVersionDB.has_icon)
        )
        rows: List[Tuple[MarketAssetDB, Optional[str], bool]] = q.offset(offset).limit(page_size).all()

        return rows, total

    def get_assets_with_file_paths(
        self,
        asset_ids: List[str],
        *,
        viewer: "ViewerContext",
    ) -> List[Tuple[MarketAssetDB, Optional[str], bool]]:
        """Batch-fetch assets by asset_id list + their latest-version file_path and has_icon.

        Excludes OFFLINE assets. Result order is database-defined; caller must
        re-sort by the original asset_ids sequence to preserve retrieval ranking.
        """
        if not asset_ids:
            return []
        q = (
            self.query()
            .filter(
                MarketAssetDB.asset_id.in_(asset_ids),
                MarketAssetDB.status != "OFFLINE",
            )
        )
        mod_clause = skill_moderation_list_clause(viewer)
        if mod_clause is not None:
            q = q.filter(mod_clause)
        return (
            q.outerjoin(
                MarketAssetVersionDB,
                and_(
                    MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                    MarketAssetVersionDB.version == list_icon_version_join_expr(viewer),
                ),
            )
            .add_columns(MarketAssetVersionDB.file_path, MarketAssetVersionDB.has_icon)
            .all()
        )

    def delete_asset(self, asset_id: str) -> int:
        """Delete asset by asset_id. Returns number of rows deleted (0 or 1)."""
        n = self.query().filter(MarketAssetDB.asset_id == asset_id).delete(synchronize_session=False)
        self.db.commit()
        return n

    def increase_install_count_atomic(self, asset_id: str, now_ms: Optional[int] = None) -> int:
        """
        原子计数更新：install_count = install_count + 1
        返回受影响行数。
        """
        ts = now_ms if now_ms is not None else int(time.time() * 1000)
        return (
            self.query()
            .filter(MarketAssetDB.asset_id == asset_id)
            .update(
                {
                    MarketAssetDB.install_count: MarketAssetDB.install_count + 1,
                    MarketAssetDB.update_time: ts,
                },
                synchronize_session=False,
            )
        )

    def get_counts_batch(self, asset_ids: List[str]) -> Dict[str, Dict[str, int]]:
        """批量查 like_count / star_count，返回 {asset_id: {like_count, star_count}}。"""
        if not asset_ids:
            return {}
        rows = (
            self.db.query(
                MarketAssetDB.asset_id,
                MarketAssetDB.like_count,
                MarketAssetDB.star_count,
            )
            .filter(MarketAssetDB.asset_id.in_(asset_ids))
            .all()
        )
        return {
            r.asset_id: {
                "like_count": int(r.like_count or 0),
                "star_count": int(r.star_count or 0),
            }
            for r in rows
        }

    def lock_for_update(self, asset_id: str) -> Optional[MarketAssetDB]:
        """SELECT ... FOR UPDATE：锁定资产行用于 like 计数的强一致更新。"""
        return (
            self.query()
            .filter(MarketAssetDB.asset_id == asset_id)
            .with_for_update()
            .first()
        )

    def increase_view_count_atomic(self, asset_id: str) -> int:
        """原子递增 view_count；不修改 update_time，避免影响列表按更新时间排序。"""
        return (
            self.query()
            .filter(MarketAssetDB.asset_id == asset_id)
            .update(
                {MarketAssetDB.view_count: MarketAssetDB.view_count + 1},
                synchronize_session=False,
            )
        )


class MarketAssetVersionRepository(MarketBaseRepository[MarketAssetVersionDB]):
    """Data access for market_asset_versions."""

    def __init__(self, db: Session):
        super().__init__(db, MarketAssetVersionDB)

    def asset_has_explicit_approved_moderation_version(self, asset_id: str) -> bool:
        """是否存在 moderation_status 显式为 APPROVED 的版本行。"""
        row = (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
            )
            .limit(1)
            .first()
        )
        return row is not None

    def asset_has_explicit_pending_moderation_version(self, asset_id: str) -> bool:
        """是否存在 moderation_status 为 PENDING 的版本行（非空值兼容语义）。"""
        row = (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_PENDING,
            )
            .limit(1)
            .first()
        )
        return row is not None

    def list_versions(self, asset_id: str) -> List[MarketAssetVersionDB]:
        return (
            self.filter_by(asset_id=asset_id)
            .order_by(MarketAssetVersionDB.create_time.desc())
            .all()
        )

    def list_version_strings_by_asset_ids(self, asset_ids: List[str]) -> Dict[str, List[str]]:
        """按 asset_id 聚合版本号字符串；每个资产内按 create_time、version 升序（与发布时间线一致）。"""
        if not asset_ids:
            return {}
        unique_ids = list(dict.fromkeys(asset_ids))
        rows = (
            self.query()
            .filter(MarketAssetVersionDB.asset_id.in_(unique_ids))
            .order_by(
                MarketAssetVersionDB.asset_id.asc(),
                MarketAssetVersionDB.create_time.asc(),
                MarketAssetVersionDB.version.asc(),
            )
            .all()
        )
        out: Dict[str, List[str]] = defaultdict(list)
        for row in rows:
            out[row.asset_id].append(row.version)
        return dict(out)

    def list_all_by_asset_ids(self, asset_ids: List[str]) -> List[MarketAssetVersionDB]:
        """批量拉取多个 asset 下的全部版本行（升序），供列表填充 all_versions / 审核标记。"""
        if not asset_ids:
            return []
        unique_ids = list(dict.fromkeys(asset_ids))
        return (
            self.query()
            .filter(MarketAssetVersionDB.asset_id.in_(unique_ids))
            .order_by(
                MarketAssetVersionDB.asset_id.asc(),
                MarketAssetVersionDB.create_time.asc(),
                MarketAssetVersionDB.version.asc(),
            )
            .all()
        )

    def asset_ids_with_pending_moderation_version(self, asset_ids: List[str]) -> set:
        """存在 moderation_status=PENDING 版本的 asset_id 集合（技能待审列表补充）。"""
        if not asset_ids:
            return set()
        unique_ids = list(dict.fromkeys(asset_ids))
        rows = (
            self.db.query(MarketAssetVersionDB.asset_id)
            .filter(
                MarketAssetVersionDB.asset_id.in_(unique_ids),
                or_(
                    MarketAssetVersionDB.publish_result == "pending_moderation",
                    and_(
                        or_(
                            MarketAssetVersionDB.publish_result.is_(None),
                            MarketAssetVersionDB.publish_result == "",
                        ),
                        MarketAssetVersionDB.moderation_status == "PENDING",
                    ),
                ),
            )
            .distinct()
            .all()
        )
        return {str(r[0]) for r in rows if r[0] is not None}

    def list_versions_chronological(self, asset_id: str) -> List[MarketAssetVersionDB]:
        """Oldest first — used to build cumulative changelog.log from all version rows."""
        return (
            self.filter_by(asset_id=asset_id)
            .order_by(
                MarketAssetVersionDB.create_time.asc(),
                MarketAssetVersionDB.version.asc(),
            )
            .all()
        )

    def get_version(
        self,
        asset_id: str,
        version: str,
    ) -> Optional[MarketAssetVersionDB]:
        return self.filter_by(asset_id=asset_id, version=version).first()

    def get_latest_version(self, asset_id: str) -> Optional[MarketAssetVersionDB]:
        return (
            self.filter_by(asset_id=asset_id)
            .order_by(MarketAssetVersionDB.create_time.desc())
            .first()
        )

    def count_versions(self, asset_id: str) -> int:
        return self.filter_by(asset_id=asset_id).count()

    def delete_version(self, asset_id: str, version: str) -> int:
        """Delete one version by asset_id and version. Returns number of rows deleted (0 or 1)."""
        n = (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.version == version,
            )
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return n

    def delete_all_versions(self, asset_id: str) -> int:
        """Delete all versions of an asset. Returns number of rows deleted."""
        n = self.query().filter(MarketAssetVersionDB.asset_id == asset_id).delete(synchronize_session=False)
        self.db.commit()
        return n

    def create_version(self, params: AssetVersionCreate) -> MarketAssetVersionDB:
        now_ms = int(time.time() * 1000)
        obj_in = params.model_dump()
        obj_in["create_time"] = now_ms
        return self.create(obj_in)


class PluginFetchRecordRepository(MarketBaseRepository[PluginFetchRecordDB]):
    """Data access for plugin_fetch_records."""

    def __init__(self, db: Session):
        super().__init__(db, PluginFetchRecordDB)

    def create_fetch_record(
        self,
        *,
        asset_id: str,
        version_id: str,
        fetch_user_id: Optional[str] = None,
        create_time: Optional[int] = None,
    ) -> PluginFetchRecordDB:
        now_ms = create_time if create_time is not None else int(time.time() * 1000)
        row = PluginFetchRecordDB(
            asset_id=asset_id,
            version_id=version_id,
            fetch_user_id=fetch_user_id,
            create_time=now_ms,
        )
        self.db.add(row)
        return row


class MarketSkillReviewRepository(MarketBaseRepository[MarketSkillReviewDB]):
    """Data access for market_skill_reviews."""

    def __init__(self, db: Session):
        super().__init__(db, MarketSkillReviewDB)

    def delete_by_asset_id(self, asset_id: str) -> int:
        n = (
            self.query()
            .filter(MarketSkillReviewDB.asset_id == asset_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return n

    def delete_by_version_id(self, version_id: str) -> int:
        n = (
            self.query()
            .filter(MarketSkillReviewDB.version_id == version_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return n


class MarketAssetInteractionRepository(MarketBaseRepository[MarketAssetInteractionDB]):
    """Data access for market_asset_interactions (like / star)."""

    def __init__(self, db: Session):
        super().__init__(db, MarketAssetInteractionDB)

    def get_interaction(
        self, asset_id: str, user_id: str, action_type: str
    ) -> Optional[MarketAssetInteractionDB]:
        return (
            self.query()
            .filter(
                MarketAssetInteractionDB.asset_id == asset_id,
                MarketAssetInteractionDB.user_id == user_id,
                MarketAssetInteractionDB.action_type == action_type,
            )
            .first()
        )

    def get_user_interactions(
        self, asset_id: str, user_id: str
    ) -> List[MarketAssetInteractionDB]:
        return (
            self.query()
            .filter(
                MarketAssetInteractionDB.asset_id == asset_id,
                MarketAssetInteractionDB.user_id == user_id,
            )
            .all()
        )

    def add_interaction(
        self, asset_id: str, user_id: str, action_type: str
    ) -> MarketAssetInteractionDB:
        """db.add() 不 commit，由调用方统一提交。"""
        now_ms = int(time.time() * 1000)
        row = MarketAssetInteractionDB(
            asset_id=asset_id,
            user_id=user_id,
            action_type=action_type,
            create_time=now_ms,
            update_time=now_ms,
        )
        self.db.add(row)
        return row

    def remove_interaction(
        self, asset_id: str, user_id: str, action_type: str
    ) -> int:
        """delete 不 commit，由调用方统一提交。返回受影响行数。"""
        return (
            self.query()
            .filter(
                MarketAssetInteractionDB.asset_id == asset_id,
                MarketAssetInteractionDB.user_id == user_id,
                MarketAssetInteractionDB.action_type == action_type,
            )
            .delete(synchronize_session=False)
        )

    def get_user_interactions_batch(
        self, asset_ids: List[str], user_id: str
    ) -> set:
        """批量查用户对多个资产的交互，返回 {(asset_id, action_type)} 集合。"""
        if not asset_ids or not user_id:
            return set()
        rows = (
            self.db.query(
                MarketAssetInteractionDB.asset_id,
                MarketAssetInteractionDB.action_type,
            )
            .filter(
                MarketAssetInteractionDB.asset_id.in_(asset_ids),
                MarketAssetInteractionDB.user_id == user_id,
            )
            .all()
        )
        return {(r.asset_id, r.action_type) for r in rows}

    def list_assets_by_user_action(
        self,
        user_id: str,
        action_type: str,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[MarketAssetDB], int]:
        """返回用户点赞/收藏的资产列表（联表 market_assets），按交互时间降序。"""
        page = max(1, page)
        page_size = max(1, min(page_size, 100))

        q = (
            self.db.query(MarketAssetDB)
            .join(
                MarketAssetInteractionDB,
                and_(
                    MarketAssetInteractionDB.asset_id.collate("utf8mb4_unicode_ci") == MarketAssetDB.asset_id,
                    MarketAssetInteractionDB.user_id == user_id,
                    MarketAssetInteractionDB.action_type == action_type,
                ),
            )
            .filter(MarketAssetDB.status != "OFFLINE")
            .order_by(MarketAssetInteractionDB.create_time.desc())
        )
        # 我的点赞/收藏：skill / teamskills 仅展示审核通过；其它类型资产不受影响。
        pt = func.lower(func.coalesce(MarketAssetDB.plugin_type, ""))
        is_skill_like = or_(pt == "skill", pt == "teamskills")
        has_publish_result = and_(
            MarketAssetDB.publish_result.isnot(None),
            MarketAssetDB.publish_result != "",
        )
        public_version_exists = and_(
            MarketAssetDB.public_latest_version.isnot(None),
            MarketAssetDB.public_latest_version != "",
        )
        new_skill_model = and_(is_skill_like, or_(has_publish_result, public_version_exists))
        pend_ver_explicit = exists(
            select(1)
            .select_from(MarketAssetVersionDB)
            .where(
                MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_PENDING,
            )
            .correlate(MarketAssetDB)
        )
        appr_ver_explicit = exists(
            select(1)
            .select_from(MarketAssetVersionDB)
            .where(
                MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
            )
            .correlate(MarketAssetDB)
        )
        main_null_or_empty = or_(
            MarketAssetDB.moderation_status.is_(None),
            MarketAssetDB.moderation_status == "",
        )
        legacy_skill_ok = or_(
            and_(main_null_or_empty, ~pend_ver_explicit),
            and_(main_null_or_empty, pend_ver_explicit, appr_ver_explicit),
            MarketAssetDB.moderation_status == MODERATION_APPROVED,
        )
        q = q.filter(
            or_(
                and_(pt != "skill", pt != "teamskills"),
                and_(new_skill_model, public_version_exists),
                and_(is_skill_like, not_(new_skill_model), legacy_skill_ok),
            )
        )

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()
        return items, total
