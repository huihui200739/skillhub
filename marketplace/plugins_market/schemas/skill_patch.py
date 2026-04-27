from dataclasses import dataclass
from typing import Any, Optional

from fastapi import UploadFile
from pydantic import BaseModel, Field


@dataclass
class SkillPatchPublishForm:
    file: UploadFile
    checksum: str
    patch_version: Optional[str]
    source_skill_version: Optional[str]
    version_desc: Optional[str]
    patch_type: str
    metadata: Optional[dict[str, Any]]
    force: bool


class SkillPatchPublishResult(BaseModel):
    patch_id: str
    skill_asset_id: str
    patch_version: str
    source_skill_version: Optional[str] = None
    patch_type: str
    status: str
    published_at: str
    storage_url: str


class SkillPatchItem(BaseModel):
    patch_id: str
    skill_asset_id: str
    source_skill_version: Optional[str] = None
    patch_version: str
    patch_type: str
    publisher_id: str
    publisher_name: str
    changelog: Optional[str] = None
    status: Optional[str] = None
    file_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    icon_uri: Optional[str] = None
    create_time: Optional[int] = None
    update_time: Optional[int] = None


class SkillPatchListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[SkillPatchItem]


class SkillPatchDetail(BaseModel):
    patch_id: str
    skill_asset_id: str
    skill_name: str
    skill_display_name: str
    source_skill_version: Optional[str] = None
    patch_version: str
    patch_type: str
    publisher_id: str
    publisher_name: str
    changelog: Optional[str] = None
    status: Optional[str] = None
    file_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    icon_uri: Optional[str] = None
    create_time: Optional[int] = None
    update_time: Optional[int] = None


class SkillPatchDeleteData(BaseModel):
    skill_asset_id: str
    patch_version: str = Field(..., description='具体版本号或 "all"')


class SkillPatchDownloadData(BaseModel):
    download_url: str
    skill_asset_id: str
    patch_id: str
    patch_version: str
    file_size: int
    checksum_sha256: str

