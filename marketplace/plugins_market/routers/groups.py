# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.database import get_db
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupDiscoverQuery,
    GroupGrantableSkillListResponse,
    GroupGrantableSkillQuery,
    GroupItem,
    GroupJoinRequestCreate,
    GroupJoinRequestDecision,
    GroupJoinRequestItem,
    GroupJoinRequestListResponse,
    GroupListQuery,
    GroupListResponse,
    GroupMemberItem,
    GroupMemberListResponse,
    GroupMemberUpsertRequest,
    GroupSkillGrantDecision,
    GroupSkillGrantItem,
    GroupSkillGrantListResponse,
    GroupSkillGrantRequest,
    GroupSkillListQuery,
    GroupStatusListQuery,
    MyGroupSkillListResponse,
    GroupUpdateRequest,
)
from plugins_market.services.groups import (
    create_group_service,
    create_join_request_service,
    decide_group_skill_grant_service,
    decide_join_request_service,
    delete_group_service,
    discover_groups_service,
    get_group_service,
    grant_skill_to_group_service,
    list_group_grants_service,
    list_group_members_service,
    list_join_requests_service,
    list_my_groups_service,
    list_my_group_skills_service,
    remove_group_member_service,
    revoke_skill_from_group_service,
    search_grantable_skills_service,
    update_group_service,
    upsert_group_member_service,
)

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=ResponseModel[GroupItem])
async def create_group(
    body: GroupCreateRequest, db: Session = Depends(get_db), auth: AuthContext = Depends(require_auth)
) -> ResponseModel[GroupItem]:
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=create_group_service(body, auth, db))


@router.get("/my", response_model=ResponseModel[GroupListResponse])
async def list_my_groups(
    query: Annotated[GroupListQuery, Query()],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_my_groups_service(
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
            role_filter=query.role,
            sort=query.sort,
        ),
    )


@router.get("/my/skills", response_model=ResponseModel[MyGroupSkillListResponse])
async def list_my_group_skills(
    query: Annotated[GroupSkillListQuery, Query()],
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[MyGroupSkillListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_my_group_skills_service(
            auth,
            db,
            storage,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
        ),
    )


@router.get("/discover", response_model=ResponseModel[GroupListResponse])
async def discover_groups(
    query: Annotated[GroupDiscoverQuery, Query()],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=discover_groups_service(
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
            filter_by=query.filter_by,
            sort=query.sort,
        ),
    )


@router.get("/grantable-skills", response_model=ResponseModel[GroupGrantableSkillListResponse])
async def search_grantable_skills(
    query: Annotated[GroupGrantableSkillQuery, Query()],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupGrantableSkillListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=search_grantable_skills_service(
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
            group_id=query.group_id,
        ),
    )


@router.get("/{group_id}", response_model=ResponseModel[GroupItem])
async def get_group(
    group_id: str = Path(..., min_length=1), db: Session = Depends(get_db), auth: AuthContext = Depends(require_auth)
) -> ResponseModel[GroupItem]:
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=get_group_service(group_id, auth, db))


@router.patch("/{group_id}", response_model=ResponseModel[GroupItem])
async def update_group(
    body: GroupUpdateRequest,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupItem]:
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=update_group_service(group_id, body, auth, db))


@router.delete("/{group_id}", response_model=ResponseModel[dict])
async def delete_group(
    group_id: str = Path(..., min_length=1), db: Session = Depends(get_db), auth: AuthContext = Depends(require_auth)
) -> ResponseModel[dict]:
    delete_group_service(group_id, auth, db)
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"group_id": group_id})


@router.get("/{group_id}/members", response_model=ResponseModel[GroupMemberListResponse])
async def list_group_members(
    group_id: str = Path(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupMemberListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_group_members_service(group_id, auth, db, page=page, page_size=page_size),
    )


@router.put("/{group_id}/members", response_model=ResponseModel[GroupMemberItem])
async def upsert_group_member(
    body: GroupMemberUpsertRequest,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupMemberItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=upsert_group_member_service(group_id, body, auth, db)
    )


@router.delete("/{group_id}/members/{user_id}", response_model=ResponseModel[dict])
async def remove_group_member(
    group_id: str = Path(..., min_length=1),
    user_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[dict]:
    remove_group_member_service(group_id, user_id, auth, db)
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"group_id": group_id, "user_id": user_id})


@router.post("/{group_id}/join-requests", response_model=ResponseModel[GroupJoinRequestItem])
async def create_join_request(
    body: GroupJoinRequestCreate,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupJoinRequestItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=create_join_request_service(group_id, body, auth, db)
    )


@router.get("/{group_id}/join-requests", response_model=ResponseModel[GroupJoinRequestListResponse])
async def list_join_requests(
    query: Annotated[GroupStatusListQuery, Query()],
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupJoinRequestListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_join_requests_service(
            group_id,
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            status_filter=query.status,
        ),
    )


@router.post("/{group_id}/join-requests/{request_id}/decision", response_model=ResponseModel[GroupJoinRequestItem])
async def decide_join_request(
    body: GroupJoinRequestDecision,
    group_id: str = Path(..., min_length=1),
    request_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupJoinRequestItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=decide_join_request_service(group_id, request_id, body, auth, db)
    )


@router.get("/{group_id}/grants", response_model=ResponseModel[GroupSkillGrantListResponse])
async def list_group_grants(
    query: Annotated[GroupStatusListQuery, Query()],
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupSkillGrantListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_group_grants_service(
            group_id,
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            status_filter=query.status,
        ),
    )


@router.post("/{group_id}/grants", response_model=ResponseModel[GroupSkillGrantItem])
async def grant_skill_to_group(
    body: GroupSkillGrantRequest,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupSkillGrantItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=grant_skill_to_group_service(group_id, body, auth, db)
    )


@router.post("/{group_id}/grants/{asset_id}/decision", response_model=ResponseModel[GroupSkillGrantItem])
async def decide_group_skill_grant(
    body: GroupSkillGrantDecision,
    group_id: str = Path(..., min_length=1),
    asset_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupSkillGrantItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=decide_group_skill_grant_service(group_id, asset_id, body, auth, db)
    )


@router.delete("/{group_id}/grants/{asset_id}", response_model=ResponseModel[dict])
async def revoke_skill_from_group(
    group_id: str = Path(..., min_length=1),
    asset_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[dict]:
    revoke_skill_from_group_service(group_id, asset_id, auth, db)
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"group_id": group_id, "asset_id": asset_id})
