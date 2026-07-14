# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import hashlib
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.services.plugin import _list_item_from_asset
from plugins_market.core.errors import http_error_payload
from plugins_market.core.moderation import is_skill_like_plugin_type
from plugins_market.models.groups import (
    MarketGroupDB,
    MarketGroupJoinRequestDB,
    MarketGroupMemberDB,
    MarketGroupSkillGrantDB,
)
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories.market_assets_repository import list_icon_version_join_expr
from plugins_market.repositories import MarketAssetRepository, MarketAssetVersionRepository
from plugins_market.repositories.groups_repository import (
    GROUP_ROLE_MEMBER,
    GROUP_ROLE_OWNER,
    GROUP_VISIBILITY_LISTED,
    GROUP_VISIBILITY_PRIVATE,
    JOIN_STATUS_APPROVED,
    JOIN_STATUS_PENDING,
    GRANT_STATUS_ACTIVE,
    GRANT_STATUS_PENDING,
    GRANT_STATUS_REJECTED,
    GRANT_STATUS_REVOKED,
    MarketGroupJoinRequestRepository,
    MarketGroupMemberRepository,
    MarketGroupRepository,
    MarketGroupSkillGrantRepository,
    now_ms,
)
from plugins_market.services.site_notifications import notify_group_owners_skill_grant_pending
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupGrantableSkillItem,
    GroupGrantableSkillListResponse,
    GroupItem,
    GroupJoinRequestCreate,
    GroupJoinRequestDecision,
    GroupJoinRequestItem,
    GroupJoinRequestListResponse,
    GroupListResponse,
    GroupMemberItem,
    GroupMemberListResponse,
    GroupMemberUpsertRequest,
    GroupSkillGrantDecision,
    GroupSkillGrantItem,
    GroupSkillGrantListResponse,
    GroupSkillGrantRequest,
    MyGroupSkillItem,
    MyGroupSkillListResponse,
    GroupUpdateRequest,
)


def _http_exception(status_code: int, message: str, *, error: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail=http_error_payload(status_code=status_code, message=message, error=error)
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _page(page: int) -> int:
    return max(1, int(page or 1))


def _page_size(page_size: int) -> int:
    return max(1, min(int(page_size or 20), 100))


def _member_role(db: Session, group_id: str, user_id: str) -> str | None:
    row = MarketGroupMemberRepository(db).get_member(group_id, user_id)
    return row.role if row else None


def _viewer_group_role(db: Session, group_id: str, auth: AuthContext) -> str | None:
    if auth.is_admin:
        return GROUP_ROLE_OWNER
    return _member_role(db, group_id, auth.acting_user_id)


def _can_view_group(group: MarketGroupDB, role: str | None) -> bool:
    return bool(role) or (getattr(group, "visibility", None) or GROUP_VISIBILITY_PRIVATE) == GROUP_VISIBILITY_LISTED


def _require_group(db: Session, group_id: str) -> MarketGroupDB:
    group = MarketGroupRepository(db).get_by_group_id(group_id)
    if not group or group.status != "active":
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Group not found", error="group_not_found")
    return group


def _require_member(db: Session, group_id: str, auth: AuthContext) -> str:
    role = _viewer_group_role(db, group_id, auth)
    if not role:
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied")
    return role


def _require_owner(db: Session, group_id: str, auth: AuthContext) -> None:
    role = _require_member(db, group_id, auth)
    if role != GROUP_ROLE_OWNER:
        raise _http_exception(
            status.HTTP_403_FORBIDDEN, "Only group owner can perform this operation", error="permission_denied"
        )


def _group_item(row: MarketGroupDB, viewer_role: str | None = None, join_status: str | None = None) -> GroupItem:
    return GroupItem(
        group_id=row.group_id,
        name=row.name,
        description=row.description,
        owner_id=row.owner_id,
        owner_name=row.owner_name,
        visibility=getattr(row, "visibility", None) or "private",
        member_count=int(row.member_count or 0),
        skill_count=int(row.skill_count or 0),
        viewer_role=viewer_role,
        join_request_status=join_status,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or 0),
    )


def _member_item(row: MarketGroupMemberDB) -> GroupMemberItem:
    return GroupMemberItem(
        user_id=row.user_id,
        user_name=row.user_name,
        role=row.role,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or 0),
    )


def _join_request_item(row: MarketGroupJoinRequestDB) -> GroupJoinRequestItem:
    return GroupJoinRequestItem(
        request_id=row.request_id,
        group_id=row.group_id,
        user_id=row.user_id,
        user_name=row.user_name,
        message=row.message,
        status=row.status,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or 0),
    )


def _grant_item(
    row: MarketGroupSkillGrantDB, asset: MarketAssetDB | None = None, viewer_access_source: str | None = None
) -> GroupSkillGrantItem:
    return GroupSkillGrantItem(
        group_id=row.group_id,
        asset_id=row.asset_id,
        skill_name=asset.name if asset else None,
        skill_display_name=asset.display_name if asset else None,
        icon_uri=None,
        latest_version=asset.latest_version if asset else None,
        public_latest_version=asset.public_latest_version if asset else None,
        status=row.status or GRANT_STATUS_ACTIVE,
        viewer_access_source=viewer_access_source,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or row.create_time or 0),
    )


def _grantable_skill_item(row: MarketAssetDB, grant_status: str | None = None) -> GroupGrantableSkillItem:
    return GroupGrantableSkillItem(
        asset_id=row.asset_id,
        name=row.name,
        display_name=row.display_name,
        short_desc=row.short_desc,
        publisher_id=row.publisher_id,
        publisher_name=row.publisher_name,
        plugin_type=row.plugin_type,
        latest_version=row.latest_version,
        group_grant_status=grant_status,
    )


def create_group_service(body: GroupCreateRequest, auth: AuthContext, db: Session) -> GroupItem:
    ts = now_ms()
    group = MarketGroupDB(
        group_id=_new_id("grp"),
        name=body.name,
        description=body.description,
        owner_id=auth.acting_user_id,
        owner_name=auth.acting_user_name,
        visibility=body.visibility,
        status="active",
        member_count=1,
        skill_count=0,
        create_time=ts,
        update_time=ts,
    )
    member = MarketGroupMemberDB(
        group_id=group.group_id,
        user_id=auth.acting_user_id,
        user_name=auth.acting_user_name,
        role=GROUP_ROLE_OWNER,
        create_time=ts,
        update_time=ts,
    )
    try:
        db.add(group)
        db.add(member)
        db.commit()
        db.refresh(group)
        return _group_item(group, GROUP_ROLE_OWNER)
    except SQLAlchemyError:
        db.rollback()
        raise


def list_my_groups_service(
    auth: AuthContext,
    db: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    role_filter: str | None = None,
    sort: str | None = None,
) -> GroupListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    safe_role = role_filter if role_filter in (GROUP_ROLE_OWNER, GROUP_ROLE_MEMBER) else None
    safe_sort = sort if sort in ("updated", "members", "skills", "name") else None
    rows, total = MarketGroupRepository(db).list_for_user(
        auth.acting_user_id, keyword, page=safe_page, page_size=safe_size, role_filter=safe_role, sort=safe_sort
    )
    return GroupListResponse(
        page=safe_page, page_size=safe_size, total=total, items=[_group_item(row, role) for row, role in rows]
    )


def discover_groups_service(
    auth: AuthContext,
    db: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None,
    filter_by: str | None = None,
    sort: str | None = None,
) -> GroupListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    safe_filter = filter_by if filter_by in ("joined", "pending", "available") else None
    safe_sort = sort if sort in ("updated", "members", "skills", "name") else None
    rows, total = MarketGroupRepository(db).discover(
        auth.acting_user_id, keyword, page=safe_page, page_size=safe_size, filter_by=safe_filter, sort=safe_sort
    )
    return GroupListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=[_group_item(row, role, join_status) for row, role, join_status in rows],
    )


def get_group_service(group_id: str, auth: AuthContext, db: Session) -> GroupItem:
    group = _require_group(db, group_id)
    role = _viewer_group_role(db, group_id, auth)
    if not _can_view_group(group, role):
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied")
    latest = MarketGroupJoinRequestRepository(db).latest_for_user(group_id, auth.acting_user_id)
    return _group_item(group, role, latest.status if latest else None)


def update_group_service(group_id: str, body: GroupUpdateRequest, auth: AuthContext, db: Session) -> GroupItem:
    group = _require_group(db, group_id)
    _require_owner(db, group_id, auth)
    if body.name is not None:
        group.name = body.name
    if body.description is not None:
        group.description = body.description
    if body.visibility is not None:
        group.visibility = body.visibility
    group.update_time = now_ms()
    try:
        db.add(group)
        db.commit()
        db.refresh(group)
        return _group_item(group, _member_role(db, group_id, auth.acting_user_id))
    except SQLAlchemyError:
        db.rollback()
        raise


def delete_group_service(group_id: str, auth: AuthContext, db: Session) -> None:
    _require_group(db, group_id)
    _require_owner(db, group_id, auth)
    group_repo = MarketGroupRepository(db)
    member_repo = MarketGroupMemberRepository(db)
    join_repo = MarketGroupJoinRequestRepository(db)
    grant_repo = MarketGroupSkillGrantRepository(db)
    try:
        grant_repo.delete_by_group(group_id)
        join_repo.delete_by_group(group_id)
        member_repo.delete_by_group(group_id)
        group_repo.delete_group(group_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def list_group_members_service(
    group_id: str, auth: AuthContext, db: Session, *, page: int, page_size: int
) -> GroupMemberListResponse:
    _require_group(db, group_id)
    _require_member(db, group_id, auth)
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    rows, total = MarketGroupMemberRepository(db).list_members(group_id, page=safe_page, page_size=safe_size)
    return GroupMemberListResponse(
        page=safe_page, page_size=safe_size, total=total, items=[_member_item(r) for r in rows]
    )


def upsert_group_member_service(
    group_id: str, body: GroupMemberUpsertRequest, auth: AuthContext, db: Session
) -> GroupMemberItem:
    _require_group(db, group_id)
    _require_owner(db, group_id, auth)
    member_repo = MarketGroupMemberRepository(db)
    existing = member_repo.get_member(group_id, body.user_id)
    if existing and existing.role == GROUP_ROLE_OWNER and body.role != GROUP_ROLE_OWNER:
        raise _http_exception(status.HTTP_400_BAD_REQUEST, "Cannot demote group owner", error="cannot_demote_owner")
    try:
        row = member_repo.upsert_member(
            group_id=group_id, user_id=body.user_id, user_name=body.user_name, role=body.role
        )
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(row)
        return _member_item(row)
    except IntegrityError as exc:
        db.rollback()
        raise _http_exception(status.HTTP_409_CONFLICT, "Member already exists", error="member_conflict") from exc
    except SQLAlchemyError:
        db.rollback()
        raise


def remove_group_member_service(group_id: str, user_id: str, auth: AuthContext, db: Session) -> None:
    _require_group(db, group_id)
    member_repo = MarketGroupMemberRepository(db)
    row = member_repo.get_member(group_id, user_id)
    if not row:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Member not found", error="member_not_found")
    if user_id != auth.acting_user_id:
        _require_owner(db, group_id, auth)
    if row.role == GROUP_ROLE_OWNER:
        raise _http_exception(status.HTTP_400_BAD_REQUEST, "Cannot remove group owner", error="cannot_remove_owner")
    try:
        member_repo.remove_member(group_id, user_id)
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def create_join_request_service(
    group_id: str, body: GroupJoinRequestCreate, auth: AuthContext, db: Session
) -> GroupJoinRequestItem:
    group = _require_group(db, group_id)
    if (getattr(group, "visibility", None) or GROUP_VISIBILITY_PRIVATE) != GROUP_VISIBILITY_LISTED:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Group not found", error="group_not_found")
    member_repo = MarketGroupMemberRepository(db)
    if member_repo.get_member(group_id, auth.acting_user_id):
        raise _http_exception(status.HTTP_409_CONFLICT, "User is already a group member", error="already_member")
    join_repo = MarketGroupJoinRequestRepository(db)
    existing = join_repo.get_pending(group_id, auth.acting_user_id)
    if existing:
        return _join_request_item(existing)
    ts = now_ms()
    row = MarketGroupJoinRequestDB(
        request_id=_new_id("gjr"),
        group_id=group_id,
        user_id=auth.acting_user_id,
        user_name=auth.acting_user_name,
        message=body.message,
        status=JOIN_STATUS_PENDING,
        create_time=ts,
        update_time=ts,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
        return _join_request_item(row)
    except IntegrityError:
        db.rollback()
        existing = join_repo.get_pending(group_id, auth.acting_user_id)
        if existing:
            return _join_request_item(existing)
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def list_join_requests_service(
    group_id: str, auth: AuthContext, db: Session, *, page: int, page_size: int, status_filter: str | None
) -> GroupJoinRequestListResponse:
    _require_group(db, group_id)
    _require_owner(db, group_id, auth)
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    rows, total = MarketGroupJoinRequestRepository(db).list_for_group(
        group_id, page=safe_page, page_size=safe_size, status=status_filter
    )
    return GroupJoinRequestListResponse(
        page=safe_page, page_size=safe_size, total=total, items=[_join_request_item(r) for r in rows]
    )


def decide_join_request_service(
    group_id: str, request_id: str, body: GroupJoinRequestDecision, auth: AuthContext, db: Session
) -> GroupJoinRequestItem:
    _require_group(db, group_id)
    _require_owner(db, group_id, auth)
    join_repo = MarketGroupJoinRequestRepository(db)
    req = join_repo.get_by_request_id(request_id)
    if not req or req.group_id != group_id:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Join request not found", error="join_request_not_found")
    if req.status != JOIN_STATUS_PENDING:
        return _join_request_item(req)
    member_repo = MarketGroupMemberRepository(db)
    if body.status == JOIN_STATUS_APPROVED and member_repo.get_member(group_id, req.user_id):
        req.status = JOIN_STATUS_REJECTED
        req.operator_id = auth.acting_user_id
        req.operator_name = auth.acting_user_name
        req.update_time = now_ms()
        try:
            db.add(req)
            db.commit()
            db.refresh(req)
            return _join_request_item(req)
        except SQLAlchemyError:
            db.rollback()
            raise
    ts = now_ms()
    try:
        if body.status == JOIN_STATUS_APPROVED:
            approved = (
                db.query(MarketGroupJoinRequestDB)
                .filter(
                    MarketGroupJoinRequestDB.group_id == group_id,
                    MarketGroupJoinRequestDB.user_id == req.user_id,
                    MarketGroupJoinRequestDB.status == JOIN_STATUS_APPROVED,
                    MarketGroupJoinRequestDB.request_id != request_id,
                )
                .first()
            )
            if approved:
                member_repo.upsert_member(
                    group_id=group_id, user_id=req.user_id, user_name=req.user_name, role=GROUP_ROLE_MEMBER
                )
                db.delete(req)
                db.flush()
                MarketGroupRepository(db).refresh_counts(group_id)
                db.commit()
                return GroupJoinRequestItem(
                    request_id=request_id,
                    group_id=group_id,
                    user_id=approved.user_id,
                    user_name=req.user_name or approved.user_name,
                    message=req.message,
                    status=JOIN_STATUS_APPROVED,
                    operator_id=auth.acting_user_id,
                    operator_name=auth.acting_user_name,
                    create_time=int(req.create_time or 0),
                    update_time=ts,
                )
            member_repo.upsert_member(
                group_id=group_id, user_id=req.user_id, user_name=req.user_name, role=GROUP_ROLE_MEMBER
            )
        if body.status != JOIN_STATUS_PENDING:
            (
                db.query(MarketGroupJoinRequestDB)
                .filter(
                    MarketGroupJoinRequestDB.group_id == group_id,
                    MarketGroupJoinRequestDB.user_id == req.user_id,
                    MarketGroupJoinRequestDB.status == body.status,
                    MarketGroupJoinRequestDB.request_id != request_id,
                )
                .delete(synchronize_session=False)
            )
        req.status = body.status
        req.operator_id = auth.acting_user_id
        req.operator_name = auth.acting_user_name
        req.update_time = ts
        db.add(req)
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(req)
        return _join_request_item(req)
    except SQLAlchemyError:
        db.rollback()
        raise


def search_grantable_skills_service(
    auth: AuthContext, db: Session, *, page: int, page_size: int, keyword: str | None, group_id: str | None = None
) -> GroupGrantableSkillListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    rows, total = MarketAssetRepository(db).search_grantable_skills_for_publisher(
        publisher_id=auth.acting_user_id,
        keyword=keyword,
        page=safe_page,
        page_size=safe_size,
    )
    grant_status_by_asset_id: dict[str, str] = {}
    if group_id and rows:
        group = _require_group(db, group_id)
        role = _viewer_group_role(db, group_id, auth)
        if not _can_view_group(group, role):
            raise _http_exception(status.HTTP_404_NOT_FOUND, "Group not found", error="group_not_found")
        grants = MarketGroupSkillGrantRepository(db).grants_for_assets(group_id, [row.asset_id for row in rows])
        grant_status_by_asset_id = {
            grant.asset_id: grant.status
            for grant in grants
            if grant.status in (GRANT_STATUS_ACTIVE, GRANT_STATUS_PENDING)
        }
    return GroupGrantableSkillListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=[_grantable_skill_item(r, grant_status_by_asset_id.get(r.asset_id)) for r in rows],
    )


def _require_grantable_asset(asset: MarketAssetDB | None, auth: AuthContext) -> MarketAssetDB:
    if not asset or (asset.status or "").upper() == "OFFLINE":
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found", error="asset_not_found")
    if not is_skill_like_plugin_type(asset.plugin_type):
        raise _http_exception(
            status.HTTP_400_BAD_REQUEST, "Only skill assets can be granted to groups", error="invalid_asset_type"
        )
    if not auth.is_admin and asset.publisher_id != auth.acting_user_id:
        raise _http_exception(
            status.HTTP_403_FORBIDDEN, "Only publisher can grant this skill", error="permission_denied"
        )
    return asset


def _is_group_owner_role(role: str | None) -> bool:
    return role == GROUP_ROLE_OWNER


def grant_skill_to_group_service(
    group_id: str, body: GroupSkillGrantRequest, auth: AuthContext, db: Session
) -> GroupSkillGrantItem:
    group = _require_group(db, group_id)
    role = _viewer_group_role(db, group_id, auth)
    if not _can_view_group(group, role):
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied")
    asset = _require_grantable_asset(MarketAssetRepository(db).get_by_asset_id(body.asset_id), auth)
    grant_repo = MarketGroupSkillGrantRepository(db)
    existing = grant_repo.get_grant(group_id, asset.asset_id)
    if existing:
        if existing.status in (GRANT_STATUS_REJECTED, GRANT_STATUS_REVOKED):
            next_status = GRANT_STATUS_ACTIVE if _is_group_owner_role(role) else GRANT_STATUS_PENDING
            grant_repo.set_status(
                existing,
                status=next_status,
                operator_id=auth.acting_user_id if next_status == GRANT_STATUS_ACTIVE else None,
                operator_name=auth.acting_user_name if next_status == GRANT_STATUS_ACTIVE else None,
            )
            MarketGroupRepository(db).refresh_counts(group_id)
            db.commit()
            db.refresh(existing)
            member_repo = MarketGroupMemberRepository(db)
            if next_status == GRANT_STATUS_PENDING:
                owner_ids = member_repo.owner_user_ids_for_group(group_id, exclude_user_id=auth.acting_user_id)
                notify_group_owners_skill_grant_pending(db, owner_user_ids=owner_ids)
        return _grant_item(existing)
    ts = now_ms()
    grant_status = GRANT_STATUS_ACTIVE if _is_group_owner_role(role) else GRANT_STATUS_PENDING
    row = MarketGroupSkillGrantDB(
        group_id=group_id,
        asset_id=asset.asset_id,
        status=grant_status,
        operator_id=auth.acting_user_id if grant_status == GRANT_STATUS_ACTIVE else None,
        operator_name=auth.acting_user_name if grant_status == GRANT_STATUS_ACTIVE else None,
        create_time=ts,
        update_time=ts,
    )
    try:
        db.add(row)
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(row)
        member_repo = MarketGroupMemberRepository(db)
        if grant_status == GRANT_STATUS_PENDING:
            owner_ids = member_repo.owner_user_ids_for_group(group_id, exclude_user_id=auth.acting_user_id)
            notify_group_owners_skill_grant_pending(db, owner_user_ids=owner_ids)
        return _grant_item(row)
    except IntegrityError:
        db.rollback()
        existing = grant_repo.get_grant(group_id, asset.asset_id)
        if existing:
            return _grant_item(existing)
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def decide_group_skill_grant_service(
    group_id: str, asset_id: str, body: GroupSkillGrantDecision, auth: AuthContext, db: Session
) -> GroupSkillGrantItem:
    _require_group(db, group_id)
    _require_owner(db, group_id, auth)
    grant_repo = MarketGroupSkillGrantRepository(db)
    row = grant_repo.get_grant(group_id, asset_id)
    if not row or row.status == GRANT_STATUS_REVOKED:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Grant not found", error="grant_not_found")
    if row.status != GRANT_STATUS_PENDING:
        raise _http_exception(
            status.HTTP_409_CONFLICT, "Only pending grants can be reviewed", error="grant_not_pending"
        )
    next_status = GRANT_STATUS_ACTIVE if body.status == GRANT_STATUS_ACTIVE else GRANT_STATUS_REJECTED
    try:
        grant_repo.set_status(
            row, status=next_status, operator_id=auth.acting_user_id, operator_name=auth.acting_user_name
        )
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(row)
        return _grant_item(row)
    except SQLAlchemyError:
        db.rollback()
        raise


def revoke_skill_from_group_service(group_id: str, asset_id: str, auth: AuthContext, db: Session) -> None:
    _require_group(db, group_id)
    role = _member_role(db, group_id, auth.acting_user_id)
    grant_repo = MarketGroupSkillGrantRepository(db)
    row = grant_repo.get_grant(group_id, asset_id)
    if not row or row.status == GRANT_STATUS_REVOKED:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Grant not found", error="grant_not_found")
    asset = MarketAssetRepository(db).get_by_asset_id(asset_id)
    is_publisher = bool(asset and asset.publisher_id == auth.acting_user_id)
    if not (auth.is_admin or _is_group_owner_role(role) or is_publisher):
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied")
    try:
        grant_repo.set_status(
            row, status=GRANT_STATUS_REVOKED, operator_id=auth.acting_user_id, operator_name=auth.acting_user_name
        )
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def list_group_grants_service(
    group_id: str, auth: AuthContext, db: Session, *, page: int, page_size: int, status_filter: str | None = None
) -> GroupSkillGrantListResponse:
    group = _require_group(db, group_id)
    role = _viewer_group_role(db, group_id, auth)
    if not _can_view_group(group, role):
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied")
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    safe_status = (
        status_filter
        if status_filter in (GRANT_STATUS_PENDING, GRANT_STATUS_ACTIVE, GRANT_STATUS_REJECTED, GRANT_STATUS_REVOKED)
        else None
    )
    if safe_status != GRANT_STATUS_ACTIVE and not _is_group_owner_role(role):
        safe_status = GRANT_STATUS_ACTIVE
    viewer = ViewerContext(user_id=auth.acting_user_id, user_login=auth.acting_user_name, is_system_admin=auth.is_admin)
    grant_repo = MarketGroupSkillGrantRepository(db)
    if safe_status == GRANT_STATUS_ACTIVE:
        rows, total = grant_repo.list_for_group_with_available_assets(
            group_id,
            viewer=viewer,
            page=safe_page,
            page_size=safe_size,
            status=safe_status,
        )
    else:
        rows, total = grant_repo.list_for_group(
            group_id, page=safe_page, page_size=safe_size, status=safe_status
        )
    asset_ids = [r.asset_id for r in rows]
    assets = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id.in_(asset_ids)).all() if asset_ids else []
    asset_map = {a.asset_id: a for a in assets}
    granted_ids = grant_repo.asset_ids_granted_to_user(
        user_id=auth.acting_user_id, asset_ids=asset_ids
    )
    source_map: dict[str, str | None] = {}
    for asset in assets:
        if viewer.can_see_all_skill_moderation_states:
            source_map[asset.asset_id] = "admin"
        elif asset.publisher_id == auth.acting_user_id:
            source_map[asset.asset_id] = "owner"
        elif asset.asset_id in granted_ids:
            source_map[asset.asset_id] = "group"
        elif safe_status == GRANT_STATUS_ACTIVE or viewer.can_view_skill_asset(asset, db):
            source_map[asset.asset_id] = "public"
    return GroupSkillGrantListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=[_grant_item(r, asset_map.get(r.asset_id), source_map.get(r.asset_id)) for r in rows],
    )


def user_has_group_skill_access(db: Session, *, user_id: str | None, asset_id: str) -> bool:
    return MarketGroupSkillGrantRepository(db).user_has_asset_grant(user_id=user_id or "", asset_id=asset_id)


def visible_group_granted_asset_ids(db: Session, *, user_id: str | None, asset_ids: list[str]) -> set[str]:
    return MarketGroupSkillGrantRepository(db).asset_ids_granted_to_user(user_id=user_id or "", asset_ids=asset_ids)


def list_my_group_skills_service(
    auth: AuthContext, db: Session, storage, *, page: int, page_size: int, keyword: str | None = None
) -> MyGroupSkillListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    viewer = ViewerContext(user_id=auth.acting_user_id, user_login=auth.acting_user_name, is_system_admin=auth.is_admin)
    grant_rows, total = MarketGroupSkillGrantRepository(db).list_grants_for_user(
        user_id=auth.acting_user_id,
        page=safe_page,
        page_size=safe_size,
        keyword=keyword,
    )
    asset_ids = [row[0].asset_id for row in grant_rows]
    if not asset_ids:
        return MyGroupSkillListResponse(page=safe_page, page_size=safe_size, total=total, items=[])
    asset_rows = (
        db.query(MarketAssetDB)
        .filter(MarketAssetDB.asset_id.in_(asset_ids), MarketAssetDB.status != "OFFLINE")
        .outerjoin(
            MarketAssetVersionDB,
            and_(
                MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                MarketAssetVersionDB.version == list_icon_version_join_expr(viewer, publisher_scoped=True),
            ),
        )
        .add_columns(MarketAssetVersionDB.file_path, MarketAssetVersionDB.has_icon)
        .all()
    )
    asset_map = {asset.asset_id: (asset, file_path, bool(has_icon)) for asset, file_path, has_icon in asset_rows}
    version_repo = MarketAssetVersionRepository(db)
    vrows = version_repo.list_all_by_asset_ids(list(asset_map.keys()))
    vmap: dict[str, list] = {}
    for row in vrows:
        vmap.setdefault(row.asset_id, []).append(row)
    items: list[MyGroupSkillItem] = []
    for grant, group_name in grant_rows:
        packed = asset_map.get(grant.asset_id)
        if not packed:
            continue
        asset, file_path, has_icon = packed
        skill = _list_item_from_asset(
            asset,
            file_path,
            has_icon,
            storage,
            vmap.get(asset.asset_id, []),
            viewer,
            market_public_scoped=False,
            db=db,
        )
        items.append(
            MyGroupSkillItem(
                group_id=grant.group_id,
                group_name=group_name or grant.group_id,
                skill=skill,
            )
        )
    return MyGroupSkillListResponse(page=safe_page, page_size=safe_size, total=total, items=items)
