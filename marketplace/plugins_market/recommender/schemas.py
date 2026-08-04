"""Recommender API request / response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    user_id: str = Field("", description="User id; empty => cold-start / install TopK fallback")
    request_id: str = Field("", description="Caller request id (echoed)")
    timestamp: int | float | None = Field(None, description="Client timestamp (logged only)")
    top_k: int = Field(10, ge=1, le=500)
    category_id: str = Field(
        "",
        description="Optional root category id (e.g. software-development); empty = all",
    )


class RecommendItemOut(BaseModel):
    asset_id: str
    score: float


class RecommendData(BaseModel):
    request_id: str
    user_id: str
    source: str
    category_id: str = ""
    items: list[RecommendItemOut]


class ByIdsRequest(BaseModel):
    asset_ids: list[str] = Field(..., min_length=1)
    top_k: int = Field(10, ge=1, le=500)
    category_id: str = Field("", description="Optional category filter for Milvus search")


class ByQueriesRequest(BaseModel):
    queries: list[str] = Field(..., min_length=1)
    top_k: int = Field(10, ge=1, le=500)
    category_id: str = Field("", description="Optional category filter for Milvus search")


class ScoredItem(BaseModel):
    asset_id: str
    score: float


class RerankMmrRequest(BaseModel):
    items: list[ScoredItem] = Field(..., min_length=1)
    top_k: int | None = Field(None, ge=1, le=500)


class RecommendItemsData(BaseModel):
    items: list[dict[str, Any]]
