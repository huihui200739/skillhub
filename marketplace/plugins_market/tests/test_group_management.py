import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.auth import AuthContext
from plugins_market.models.base import Base
from plugins_market.models.groups import MarketGroupSkillGrantDB
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.models.site_notifications import SiteNotificationDB
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupJoinRequestCreate,
    GroupJoinRequestDecision,
    GroupMemberUpsertRequest,
    GroupSkillGrantRequest,
    GroupSkillGrantDecision,
)
from plugins_market.services.groups import (
    create_group_service,
    create_join_request_service,
    decide_join_request_service,
    delete_group_service,
    grant_skill_to_group_service,
    decide_group_skill_grant_service,
    list_group_grants_service,
    list_group_members_service,
    list_my_groups_service,
    remove_group_member_service,
    revoke_skill_from_group_service,
    search_grantable_skills_service,
    upsert_group_member_service,
    user_has_group_skill_access,
    visible_group_granted_asset_ids,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine)
    return session_cls()


def _auth(user_id="owner", name="Owner"):
    return AuthContext(is_admin=False, acting_user_id=user_id, acting_user_name=name)


def _skill(asset_id="skill-1", publisher_id="owner"):
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="plugin",
        name=asset_id,
        display_name=asset_id,
        publisher_id=publisher_id,
        publisher_name=publisher_id,
        status="PUBLISHED",
        plugin_type="skill",
        latest_version="1.0.0",
        public_latest_version="1.0.0",
        moderation_status="APPROVED",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=1,
    )


def test_group_create_adds_owner_member():
    db = _db()

    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)

    assert group.name == "研发组"
    assert group.viewer_role == "owner"
    assert group.member_count == 1


def test_join_request_approval_adds_member():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    req = create_join_request_service(group.group_id, GroupJoinRequestCreate(message="申请"), _auth("u2", "User2"), db)

    decided = decide_join_request_service(
        group.group_id,
        req.request_id,
        GroupJoinRequestDecision(status="approved"),
        _auth(),
        db,
    )

    members = list_group_members_service(group.group_id, _auth(), db, page=1, page_size=20)
    joined = next(item for item in members.items if item.user_id == "u2")

    assert decided.status == "approved"
    assert joined.role == "member"
    assert members.total == 2


def test_join_request_approval_is_idempotent_when_user_already_joined():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    first = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="第一次"), _auth("u2", "User2"), db
    )
    decide_join_request_service(
        group.group_id, first.request_id, GroupJoinRequestDecision(status="approved"), _auth(), db
    )
    remove_group_member_service(group.group_id, "u2", _auth("u2", "User2"), db)
    second = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="第二次"), _auth("u2", "User2"), db
    )

    decided = decide_join_request_service(
        group.group_id, second.request_id, GroupJoinRequestDecision(status="approved"), _auth(), db
    )

    assert decided.status == "approved"


def test_join_request_can_be_rejected_reapplied_and_rejected_again():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    first = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="first"), _auth("u2", "User2"), db
    )
    decide_join_request_service(
        group.group_id, first.request_id, GroupJoinRequestDecision(status="rejected"), _auth(), db
    )
    second = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="second"), _auth("u2", "User2"), db
    )

    decided = decide_join_request_service(
        group.group_id, second.request_id, GroupJoinRequestDecision(status="rejected"), _auth(), db
    )

    assert decided.status == "rejected"


def test_active_group_grant_cannot_be_rejected_via_decision_endpoint():
    db = _db()
    db.add(_skill(asset_id="external-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="external-skill"), _auth("publisher", "Publisher"), db
    )
    decide_group_skill_grant_service(
        group.group_id, pending.asset_id, GroupSkillGrantDecision(status="active"), _auth(), db
    )

    with pytest.raises(HTTPException) as exc_info:
        decide_group_skill_grant_service(
            group.group_id, pending.asset_id, GroupSkillGrantDecision(status="rejected"), _auth(), db
        )
    assert exc_info.value.status_code == 409

    assert user_has_group_skill_access(db, user_id="owner", asset_id="external-skill") is True


def test_member_can_leave_group_without_admin_permission():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id, GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"), _auth(), db
    )

    remove_group_member_service(group.group_id, "u2", _auth("u2", "User2"), db)

    result = list_my_groups_service(_auth("u2", "User2"), db, page=1, page_size=20)
    assert result.total == 0


def test_owner_cannot_demote_self():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("owner", "Owner"), db)

    with pytest.raises(HTTPException) as exc_info:
        upsert_group_member_service(
            group.group_id,
            GroupMemberUpsertRequest(user_id="owner", user_name="Owner", role="member"),
            _auth("owner", "Owner"),
            db,
        )
    assert exc_info.value.status_code == 400


def test_owner_cannot_leave_group_by_removing_self():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)

    try:
        remove_group_member_service(group.group_id, "owner", _auth(), db)
        assert False
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_list_my_groups_supports_keyword_search():
    db = _db()
    create_group_service(GroupCreateRequest(name="研发组", description="核心开发"), _auth(), db)
    create_group_service(GroupCreateRequest(name="运营组", description="内容运营"), _auth(), db)

    result = list_my_groups_service(_auth(), db, page=1, page_size=20, keyword="研发")

    assert result.total == 1
    assert result.items[0].name == "研发组"


def test_list_my_groups_orders_owner_before_member():
    db = _db()
    member_group = create_group_service(
        GroupCreateRequest(name="普通成员组"), _auth("member-owner", "Member Owner"), db
    )
    owner_group = create_group_service(GroupCreateRequest(name="所有者组"), _auth("u1", "User1"), db)
    upsert_group_member_service(
        member_group.group_id,
        GroupMemberUpsertRequest(user_id="u1", user_name="User1", role="member"),
        _auth("member-owner", "Member Owner"),
        db,
    )

    result = list_my_groups_service(_auth("u1", "User1"), db, page=1, page_size=20)

    assert [item.name for item in result.items] == ["所有者组", "普通成员组"]
    assert [item.viewer_role for item in result.items] == ["owner", "member"]


def test_publisher_grant_requires_group_admin_approval_before_access():
    db = _db()
    db.add(_skill(asset_id="external-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="external-skill"), _auth("publisher", "Publisher"), db
    )

    assert pending.status == "pending"
    assert user_has_group_skill_access(db, user_id="u2", asset_id="external-skill") is False

    active = decide_group_skill_grant_service(
        group.group_id, "external-skill", GroupSkillGrantDecision(status="active"), _auth(), db
    )

    assert active.status == "active"
    assert user_has_group_skill_access(db, user_id="u2", asset_id="external-skill") is True


def test_non_member_publisher_can_request_grant_to_listed_group():
    db = _db()
    db.add(_skill(asset_id="external-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="external-skill"), _auth("publisher", "Publisher"), db
    )

    assert pending.status == "pending"


def test_active_group_grant_does_not_notify_group_members():
    db = _db()
    db.add(_skill(asset_id="alice-skill", publisher_id="alice"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="charlie", user_name="Charlie", role="member"),
        _auth("alice", "Alice"),
        db,
    )

    grant = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert grant.status == "active"
    rows = db.query(SiteNotificationDB).order_by(SiteNotificationDB.id.asc()).all()
    assert rows == []


def test_pending_group_grant_notifies_group_admins_except_actor():
    db = _db()
    db.add(_skill(asset_id="alice-skill", publisher_id="alice"))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert pending.status == "pending"
    rows = db.query(SiteNotificationDB).order_by(SiteNotificationDB.id.asc()).all()
    assert [(r.inbox_key, r.template) for r in rows] == [
        ("u:charlie", "group_skill_grant_pending")
    ]


def test_search_grantable_skills_marks_active_group_grants():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    private_other = _skill(asset_id="alice-other", publisher_id="alice")
    private_other.visibility = "private"
    db.add(private_skill)
    db.add(private_other)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    result = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert [item.asset_id for item in result.items] == ["alice-other", "alice-skill"]
    assert {item.asset_id: item.group_grant_status for item in result.items} == {
        "alice-other": None,
        "alice-skill": "active",
    }


def test_group_admin_can_withdraw_pending_group_grant_and_request_again():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    db.add(private_skill)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    revoke_skill_from_group_service(group.group_id, "alice-skill", _auth("charlie", "Charlie"), db)
    search = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )
    retry = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert pending.status == "pending"
    assert {item.asset_id: item.group_grant_status for item in search.items} == {"alice-skill": None}
    assert retry.status == "pending"


def test_skill_publisher_can_revoke_own_group_grant():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    db.add(private_skill)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    revoke_skill_from_group_service(group.group_id, "alice-skill", _auth("alice", "Alice"), db)
    search = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert {item.asset_id: item.group_grant_status for item in search.items} == {"alice-skill": None}


def test_search_grantable_skills_marks_pending_external_requests():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    private_other = _skill(asset_id="alice-other", publisher_id="alice")
    private_other.visibility = "private"
    db.add(private_skill)
    db.add(private_other)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    result = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert [item.asset_id for item in result.items] == ["alice-other", "alice-skill"]
    assert {item.asset_id: item.group_grant_status for item in result.items} == {
        "alice-other": None,
        "alice-skill": "pending",
    }


def test_search_grantable_skills_keeps_rejected_group_grants():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    db.add(private_skill)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    decide_group_skill_grant_service(
        group.group_id, "alice-skill", GroupSkillGrantDecision(status="rejected"), _auth("charlie", "Charlie"), db
    )

    result = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert [item.asset_id for item in result.items] == ["alice-skill"]


def test_approved_pending_group_grant_does_not_notify_group_members():
    db = _db()
    db.add(_skill(asset_id="publisher-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth("alice", "Alice"), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="charlie", user_name="Charlie", role="member"),
        _auth("alice", "Alice"),
        db,
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="publisher-skill"), _auth("publisher", "Publisher"), db
    )

    active = decide_group_skill_grant_service(
        group.group_id, "publisher-skill", GroupSkillGrantDecision(status="active"), _auth("alice", "Alice"), db
    )

    assert active.status == "active"
    rows = db.query(SiteNotificationDB).order_by(SiteNotificationDB.id.asc()).all()
    assert [(r.inbox_key, r.template) for r in rows] == [
        ("u:alice", "group_skill_grant_pending"),
    ]


def test_group_grants_hide_unavailable_assets():
    db = _db()
    asset = _skill(asset_id="alice-skill", publisher_id="alice")
    db.add(asset)
    db.add(MarketAssetVersionDB(version_id="v1", asset_id="alice-skill", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    asset.status = "OFFLINE"
    db.add(asset)
    db.commit()
    result = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="active"
    )
    assert result.total == 0
    assert result.items == []

    asset.status = "PUBLISHED"
    db.query(MarketAssetVersionDB).filter(MarketAssetVersionDB.asset_id == "alice-skill").delete()
    db.add(asset)
    db.commit()
    result = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="active"
    )
    assert result.total == 0
    assert result.items == []


def test_active_group_grants_filter_before_database_pagination():
    db = _db()
    unavailable = _skill(asset_id="unavailable", publisher_id="alice")
    unavailable.visibility = "private"
    available = _skill(asset_id="available", publisher_id="alice")
    available.visibility = "private"
    db.add_all([unavailable, available])
    db.add(MarketAssetVersionDB(version_id="available-v1", asset_id="available", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="available"), _auth("alice", "Alice"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="unavailable"), _auth("alice", "Alice"), db
    )

    result = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=1, status_filter="active"
    )

    assert result.total == 1
    assert [item.asset_id for item in result.items] == ["available"]


def test_offline_asset_is_not_accessible_through_group_grant_acl():
    db = _db()
    asset = _skill(asset_id="alice-skill", publisher_id="alice")
    asset.visibility = "private"
    db.add(asset)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth("alice", "Alice"),
        db,
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert user_has_group_skill_access(db, user_id="u2", asset_id="alice-skill") is True
    assert visible_group_granted_asset_ids(db, user_id="u2", asset_ids=["alice-skill"]) == {"alice-skill"}

    asset.status = "OFFLINE"
    db.add(asset)
    db.commit()

    assert user_has_group_skill_access(db, user_id="u2", asset_id="alice-skill") is False
    assert visible_group_granted_asset_ids(db, user_id="u2", asset_ids=["alice-skill"]) == set()


def test_group_delete_removes_grants_members_and_requests():
    db = _db()
    db.add(_skill())
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)

    delete_group_service(group.group_id, _auth(), db)

    assert db.query(MarketGroupSkillGrantDB).count() == 0
    assert user_has_group_skill_access(db, user_id="u2", asset_id="skill-1") is False
