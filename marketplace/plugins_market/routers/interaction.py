from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth, resolve_viewer_context
from plugins_market.core.database import get_db
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.services.plugin import _skill_visible_to_marketplace_viewer
from plugins_market.repositories import MarketAssetRepository, MarketAssetInteractionRepository
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.interaction import (
    AssetInteractionState,
    BatchInteractionResult,
    InteractRequest,
    InteractionToggleResult,
    InteractionViewResult,
    UserInteractionState,
)
from plugins_market.schemas.plugin import PluginListItem, PluginListResponse


_VALID_ACTION_TYPES = {"like", "star"}

interaction_router = APIRouter(prefix="/plugins", tags=["interactions"])


def _asset_not_found(asset_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": status.HTTP_404_NOT_FOUND,
            "data": None,
            "error": "not_found",
            "message": f"Asset {asset_id!r} not found",
        },
    )


def _skill_interact_forbidden(asset_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": status.HTTP_403_FORBIDDEN,
            "data": None,
            "error": "skill_not_approved",
            "message": f"Skill {asset_id!r} is not approved for interactions",
        },
    )


def _is_skill_asset(asset) -> bool:
    return (getattr(asset, "plugin_type", None) or "").strip().lower() == "skill"


def _is_skill_interactable(asset, db: Session) -> bool:
    if not _is_skill_asset(asset):
        return True
    return _skill_visible_to_marketplace_viewer(asset, ANONYMOUS_VIEWER, db)


def _is_self_skill(asset, user_id: str | None) -> bool:
    if not user_id or not _is_skill_asset(asset):
        return False
    publisher_id = getattr(asset, "publisher_id", None)
    if not publisher_id:
        return False
    return str(user_id) == str(publisher_id)


def _build_list_item(asset) -> PluginListItem:
    return PluginListItem(
        asset_id=asset.asset_id,
        asset_type=asset.asset_type,
        name=asset.name,
        display_name=asset.display_name,
        short_desc=asset.short_desc,
        publisher_id=asset.publisher_id,
        publisher_name=asset.publisher_name,
        tags=asset.tags,
        category_id=asset.category_id,
        category_name=asset.category_name,
        certification=asset.certification,
        plugin_type=asset.plugin_type,
        moderation_status=asset.moderation_status,
        latest_version=asset.latest_version,
        public_latest_version=asset.public_latest_version,
        view_count=asset.view_count,
        install_count=asset.install_count,
        like_count=asset.like_count,
        star_count=asset.star_count,
        review_count=asset.review_count,
        average_rating=float(asset.average_rating),
        create_time=asset.create_time,
        update_time=asset.update_time,
        pin_order=asset.pin_order,
    )


@interaction_router.get(
    "/my/stars",
    response_model=ResponseModel[PluginListResponse],
)
async def get_my_stars(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[PluginListResponse]:
    interaction_repo = MarketAssetInteractionRepository(db)
    assets, total = interaction_repo.list_assets_by_user_action(
        user_id=auth.acting_user_id,
        action_type="star",
        page=page,
        page_size=page_size,
    )
    items = [_build_list_item(a) for a in assets]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=PluginListResponse(page=page, page_size=page_size, total=total, items=items),
    )


@interaction_router.get(
    "/my/likes",
    response_model=ResponseModel[PluginListResponse],
)
async def get_my_likes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[PluginListResponse]:
    interaction_repo = MarketAssetInteractionRepository(db)
    assets, total = interaction_repo.list_assets_by_user_action(
        user_id=auth.acting_user_id,
        action_type="like",
        page=page,
        page_size=page_size,
    )
    items = [_build_list_item(a) for a in assets]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=PluginListResponse(page=page, page_size=page_size, total=total, items=items),
    )


@interaction_router.post(
    "/{asset_id}/view",
    response_model=ResponseModel[InteractionViewResult],
)
async def post_view(
    asset_id: str,
    db: Session = Depends(get_db),
) -> ResponseModel[InteractionViewResult]:
    asset_repo = MarketAssetRepository(db)
    affected = asset_repo.increase_view_count_atomic(asset_id)
    if affected == 0:
        raise _asset_not_found(asset_id)
    db.commit()
    asset = asset_repo.get_by_asset_id(asset_id)
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=InteractionViewResult(view_count=asset.view_count if asset else 0),
    )


@interaction_router.post(
    "/{asset_id}/interact",
    response_model=ResponseModel[InteractionToggleResult],
)
async def post_interact(
    asset_id: str,
    body: InteractRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[InteractionToggleResult]:
    if body.action_type not in _VALID_ACTION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": status.HTTP_400_BAD_REQUEST,
                "data": None,
                "error": "invalid_action_type",
                "message": f"action_type must be one of: {', '.join(sorted(_VALID_ACTION_TYPES))}",
            },
        )

    user_id = auth.acting_user_id
    asset_repo = MarketAssetRepository(db)
    interaction_repo = MarketAssetInteractionRepository(db)

    if body.action_type == "like":
        asset = asset_repo.lock_for_update(asset_id)
        if not asset:
            raise _asset_not_found(asset_id)
        if not _is_skill_interactable(asset, db):
            raise _skill_interact_forbidden(asset_id)
        if _is_self_skill(asset, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": status.HTTP_403_FORBIDDEN,
                    "data": None,
                    "error": "self_interaction_forbidden",
                    "message": "Cannot interact with your own skill",
                },
            )
        existing = interaction_repo.get_interaction(asset_id, user_id, "like")
        if existing:
            interaction_repo.remove_interaction(asset_id, user_id, "like")
            asset.like_count = max(0, asset.like_count - 1)
            active = False
        else:
            interaction_repo.add_interaction(asset_id, user_id, "like")
            asset.like_count += 1
            active = True
        db.commit()
        db.refresh(asset)
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=InteractionToggleResult(
                action_type="like",
                active=active,
                like_count=asset.like_count,
            ),
        )

    # star
    asset = asset_repo.lock_for_update(asset_id)
    if not asset:
        raise _asset_not_found(asset_id)
    if not _is_skill_interactable(asset, db):
        raise _skill_interact_forbidden(asset_id)
    if _is_self_skill(asset, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": status.HTTP_403_FORBIDDEN,
                "data": None,
                "error": "self_interaction_forbidden",
                "message": "Cannot interact with your own skill",
            },
        )
    existing = interaction_repo.get_interaction(asset_id, user_id, "star")
    if existing:
        interaction_repo.remove_interaction(asset_id, user_id, "star")
        asset.star_count = max(0, asset.star_count - 1)
        active = False
    else:
        interaction_repo.add_interaction(asset_id, user_id, "star")
        asset.star_count += 1
        active = True
    db.commit()
    db.refresh(asset)
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=InteractionToggleResult(action_type="star", active=active, star_count=asset.star_count),
    )


@interaction_router.get(
    "/interactions/batch",
    response_model=ResponseModel[BatchInteractionResult],
)
async def get_interactions_batch(
    asset_ids: List[str] = Query(default=[]),
    db: Session = Depends(get_db),
    viewer: ViewerContext = Depends(resolve_viewer_context),
) -> ResponseModel[BatchInteractionResult]:
    if not asset_ids:
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=BatchInteractionResult(items=[]),
        )
    if len(asset_ids) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": status.HTTP_400_BAD_REQUEST,
                "data": None,
                "error": "too_many_ids",
                "message": "asset_ids must not exceed 50 items",
            },
        )

    asset_repo = MarketAssetRepository(db)
    counts = asset_repo.get_counts_batch(asset_ids)

    user_interaction_map: dict = {}
    if viewer.user_id:
        interaction_repo = MarketAssetInteractionRepository(db)
        for asset_id, action_type in interaction_repo.get_user_interactions_batch(asset_ids, viewer.user_id):
            user_interaction_map.setdefault(asset_id, set()).add(action_type)

    items = [
        AssetInteractionState(
            asset_id=aid,
            liked="like" in user_interaction_map.get(aid, set()),
            starred="star" in user_interaction_map.get(aid, set()),
            like_count=int(counts.get(aid, {}).get("like_count") or 0),
            star_count=int(counts.get(aid, {}).get("star_count") or 0),
        )
        for aid in asset_ids
    ]

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=BatchInteractionResult(items=items),
    )


@interaction_router.get(
    "/{asset_id}/interactions",
    response_model=ResponseModel[UserInteractionState],
)
async def get_interactions(
    asset_id: str,
    db: Session = Depends(get_db),
    viewer: ViewerContext = Depends(resolve_viewer_context),
) -> ResponseModel[UserInteractionState]:
    asset_repo = MarketAssetRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    like_count = asset.like_count if asset else None
    star_count = asset.star_count if asset else None

    if not viewer.user_id:
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=UserInteractionState(liked=False, starred=False, like_count=like_count, star_count=star_count),
        )

    interaction_repo = MarketAssetInteractionRepository(db)
    rows = interaction_repo.get_user_interactions(asset_id, viewer.user_id)
    action_types = {r.action_type for r in rows}
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=UserInteractionState(
            liked="like" in action_types,
            starred="star" in action_types,
            like_count=like_count,
            star_count=star_count,
        ),
    )
