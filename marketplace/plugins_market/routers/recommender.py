# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recommender HTTP API (external + SkillHub)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.config import settings
from plugins_market.core.errors import auth_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.recommender.schemas import (
    ByIdsRequest,
    ByQueriesRequest,
    RecommendData,
    RecommendItemOut,
    RecommendItemsData,
    RecommendRequest,
    RerankMmrRequest,
)
from plugins_market.recommender.service import (
    run_recommend_by_ids,
    run_recommend_by_queries,
    run_recommend_for_user,
    run_rerank_mmr,
)
from plugins_market.schemas.common import ResponseModel

logger = get_logger(__name__)

router = APIRouter(prefix="/recommend", tags=["recommend"])


def _ensure_enabled() -> None:
    if not settings.recommender_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="recommender is disabled (set MARKET_RECOMMENDER_ENABLED=true)",
        )


def _resolve_recommend_user_id(body: RecommendRequest, auth: AuthContext) -> str:
    """
    Bind personalization to the caller identity.

    - Bearer (end user): always use token user_id. Body user_id must be empty or match.
    - X-System-Token (trusted service): may assert any body.user_id (incl. empty = cold start).
    """
    requested = (body.user_id or "").strip()
    if auth.is_admin:
        return requested
    if requested and requested != auth.acting_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=auth_error_payload(
                status_code=status.HTTP_403_FORBIDDEN,
                message="body.user_id must match the authenticated user (or be omitted)",
                error="recommend_user_mismatch",
                error_code="SKILLHUB_RECOMMEND_USER_MISMATCH",
            ),
        )
    return auth.acting_user_id


@router.post("", response_model=ResponseModel[RecommendData])
def recommend(
    body: RecommendRequest,
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[RecommendData]:
    """Personalized recommend: Redis history -> Milvus -> MMR -> install TopK fallback."""
    _ensure_enabled()
    user_id = _resolve_recommend_user_id(body, auth)
    try:
        items, source = run_recommend_for_user(
            user_id=user_id,
            top_k=body.top_k,
            request_id=body.request_id,
            timestamp=body.timestamp,
            category_id=body.category_id,
        )
    except Exception as exc:
        logger.exception("recommend failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendData(
            request_id=body.request_id or "",
            user_id=user_id,
            source=source,
            category_id=(body.category_id or "").strip(),
            items=[RecommendItemOut(asset_id=x.asset_id, score=x.score) for x in items],
        ),
    )


@router.post("/by_ids", response_model=ResponseModel[RecommendItemsData])
def recommend_by_ids_api(body: ByIdsRequest) -> ResponseModel[RecommendItemsData]:
    _ensure_enabled()
    try:
        items = run_recommend_by_ids(body.asset_ids, body.top_k, category_id=body.category_id)
    except Exception as exc:
        logger.exception("recommend by_ids failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[x.to_dict() for x in items]),
    )


@router.post("/by_queries", response_model=ResponseModel[RecommendItemsData])
def recommend_by_queries_api(body: ByQueriesRequest) -> ResponseModel[RecommendItemsData]:
    _ensure_enabled()
    try:
        items = run_recommend_by_queries(body.queries, body.top_k, category_id=body.category_id)
    except Exception as exc:
        logger.exception("recommend by_queries failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[x.to_dict() for x in items]),
    )


@router.post("/rerank_mmr", response_model=ResponseModel[RecommendItemsData])
def recommend_rerank_mmr_api(body: RerankMmrRequest) -> ResponseModel[RecommendItemsData]:
    _ensure_enabled()
    try:
        items = run_rerank_mmr([it.model_dump() for it in body.items], body.top_k)
    except Exception as exc:
        logger.exception("recommend rerank_mmr failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[x.to_dict() for x in items]),
    )
