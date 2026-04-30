# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import List, Optional
from pydantic import BaseModel, Field


class InteractRequest(BaseModel):
    action_type: str = Field(..., description="like | star")


class InteractionToggleResult(BaseModel):
    action_type: str
    active: bool
    like_count: Optional[int] = None
    star_count: Optional[int] = None


class InteractionViewResult(BaseModel):
    view_count: int


class UserInteractionState(BaseModel):
    liked: bool
    starred: bool
    like_count: Optional[int] = None
    star_count: Optional[int] = None


class AssetInteractionState(BaseModel):
    asset_id: str
    liked: bool
    starred: bool
    like_count: int = 0
    star_count: int = 0


class BatchInteractionResult(BaseModel):
    items: List[AssetInteractionState]
