import json
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, get_gitcode_user_id_and_login, require_auth
from plugins_market.core.database import get_db
from plugins_market.core.errors import PublishError
from plugins_market.core.s3_storage_client import S3StorageClient, get_storage_client
from plugins_market.routers.plugin import get_publish_auth, valid_checksum
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.skill_patch import (
    SkillPatchDeleteData,
    SkillPatchDetail,
    SkillPatchDownloadData,
    SkillPatchListResponse,
    SkillPatchPublishForm,
    SkillPatchPublishResult,
)
from plugins_market.services.skill_patch import (
    delete_skill_patch_service,
    get_skill_patch_detail_service,
    get_skill_patch_download_info,
    list_skill_patches_service,
    publish_skill_patch,
)

skill_patch_router = APIRouter(prefix="/skills", tags=["skill-patches"])


def _parse_metadata_json(raw: Optional[str]) -> dict | None:
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": 400,
                "data": None,
                "error": "invalid_metadata",
                "message": f"metadata 必须是合法 JSON object：{e}",
            },
        ) from e
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": 400,
                "data": None,
                "error": "invalid_metadata",
                "message": "metadata 必须是 JSON object",
            },
        )
    return parsed


class SkillPatchFormRequired:
    def __init__(
        self,
        file: UploadFile = File(..., description="Skill 自演进版本包（.zip）"),
        checksum: str = Depends(valid_checksum),
    ):
        self.file = file
        self.checksum = checksum


class SkillPatchFormOptional:
    def __init__(
        self,
        patch_version: Optional[str] = Form(None, description="自演进版本号；不填则读取包内 plugin.yaml version"),
        source_skill_version: Optional[str] = Form(None, description="基于哪个正式 Skill 版本演进"),
        version_desc: Optional[str] = Form(None, description="自演进版本说明"),
        patch_type: str = Form("self-evolution", description="自演进版本类型"),
        metadata: Optional[str] = Form(None, description="JSON object 字符串，记录评测分、任务 ID 等扩展信息"),
        force: bool = Form(False, description="同版本存在时是否覆盖"),
    ):
        self.patch_version = patch_version.strip() if patch_version else None
        self.source_skill_version = source_skill_version.strip() if source_skill_version else None
        self.version_desc = version_desc.strip() if version_desc else None
        self.patch_type = patch_type.strip() if patch_type else "self-evolution"
        self.metadata = _parse_metadata_json(metadata)
        self.force = force


class SkillPatchPublishContext:
    def __init__(
        self,
        db: Session = Depends(get_db),
        storage: S3StorageClient = Depends(get_storage_client),
        auth: Tuple[Optional[str], bool, Optional[str]] = Depends(get_publish_auth),
    ):
        self.db = db
        self.storage = storage
        self.auth = auth


class SkillPatchReadContext:
    def __init__(
        self,
        db: Session = Depends(get_db),
        storage: S3StorageClient = Depends(get_storage_client),
    ):
        self.db = db
        self.storage = storage


class SkillPatchListQuery:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        patch_status: Optional[str] = Query(None, description="按自演进版本状态过滤，如 ACTIVE"),
    ):
        self.page = page
        self.page_size = page_size
        self.patch_status = patch_status.strip() if patch_status else None


def build_skill_patch_form(
    required: SkillPatchFormRequired = Depends(),
    optional: SkillPatchFormOptional = Depends(),
) -> SkillPatchPublishForm:
    return SkillPatchPublishForm(
        file=required.file,
        checksum=required.checksum,
        patch_version=optional.patch_version,
        source_skill_version=optional.source_skill_version,
        version_desc=optional.version_desc,
        patch_type=optional.patch_type,
        metadata=optional.metadata,
        force=optional.force,
    )


async def _resolve_publish_identity(
    auth: Tuple[Optional[str], bool, Optional[str]],
) -> tuple[str, bool, str | None]:
    token, is_system_token, acting_user_id = auth
    publisher_name_override: str | None = None
    if not is_system_token:
        acting_user_id, publisher_name_override = await get_gitcode_user_id_and_login(token or "")
    return acting_user_id or "", is_system_token, publisher_name_override


@skill_patch_router.post(
    "/{skill_asset_id}/patches",
    response_model=ResponseModel[SkillPatchPublishResult],
)
async def create_skill_patch(
    skill_asset_id: str = Path(..., description="Skill 资产 ID"),
    form: SkillPatchPublishForm = Depends(build_skill_patch_form),
    ctx: SkillPatchPublishContext = Depends(),
):
    acting_user_id, is_admin, publisher_name_override = await _resolve_publish_identity(ctx.auth)
    content = await form.file.read()
    try:
        data = publish_skill_patch(
            skill_asset_id=skill_asset_id,
            acting_user_id=acting_user_id,
            is_admin=is_admin,
            content=content,
            filename=form.file.filename,
            expected_checksum=form.checksum,
            patch_version=form.patch_version,
            source_skill_version=form.source_skill_version,
            version_desc=form.version_desc,
            patch_type=form.patch_type,
            metadata=form.metadata,
            force=form.force,
            db=ctx.db,
            storage=ctx.storage,
            publisher_name_override=publisher_name_override,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="Publish skill patch successfully", data=data)


@skill_patch_router.put(
    "/{skill_asset_id}/patches/{patch_version}",
    response_model=ResponseModel[SkillPatchPublishResult],
)
async def update_skill_patch(
    skill_asset_id: str,
    patch_version: str,
    form: SkillPatchPublishForm = Depends(build_skill_patch_form),
    ctx: SkillPatchPublishContext = Depends(),
):
    acting_user_id, is_admin, publisher_name_override = await _resolve_publish_identity(ctx.auth)
    content = await form.file.read()
    try:
        data = publish_skill_patch(
            skill_asset_id=skill_asset_id,
            acting_user_id=acting_user_id,
            is_admin=is_admin,
            content=content,
            filename=form.file.filename,
            expected_checksum=form.checksum,
            patch_version=patch_version,
            source_skill_version=form.source_skill_version,
            version_desc=form.version_desc,
            patch_type=form.patch_type,
            metadata=form.metadata,
            force=True,
            db=ctx.db,
            storage=ctx.storage,
            publisher_name_override=publisher_name_override,
            require_existing=True,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="Update skill patch successfully", data=data)


@skill_patch_router.get(
    "/{skill_asset_id}/patches",
    response_model=ResponseModel[SkillPatchListResponse],
)
def list_skill_patches(
    skill_asset_id: str,
    query: SkillPatchListQuery = Depends(),
    ctx: SkillPatchReadContext = Depends(),
):
    try:
        data = list_skill_patches_service(
            skill_asset_id=skill_asset_id,
            page=query.page,
            page_size=query.page_size,
            status=query.patch_status,
            db=ctx.db,
            storage=ctx.storage,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@skill_patch_router.get(
    "/{skill_asset_id}/patches/{patch_version}/artifact",
    response_model=ResponseModel[SkillPatchDownloadData],
)
def get_skill_patch_artifact_download(
    skill_asset_id: str,
    patch_version: str,
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
):
    try:
        data = get_skill_patch_download_info(
            skill_asset_id=skill_asset_id,
            patch_version=patch_version,
            db=db,
            storage=storage,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@skill_patch_router.get(
    "/{skill_asset_id}/patches/{patch_version}",
    response_model=ResponseModel[SkillPatchDetail],
)
def get_skill_patch_detail(
    skill_asset_id: str,
    patch_version: str,
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
):
    try:
        data = get_skill_patch_detail_service(
            skill_asset_id=skill_asset_id,
            patch_version=patch_version,
            db=db,
            storage=storage,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@skill_patch_router.delete(
    "/{skill_asset_id}/patches/{patch_version}",
    response_model=ResponseModel[SkillPatchDeleteData],
)
def delete_skill_patch(
    skill_asset_id: str,
    patch_version: str,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
):
    try:
        data = delete_skill_patch_service(
            skill_asset_id=skill_asset_id,
            patch_version=patch_version,
            acting_user_id=auth.acting_user_id or "",
            is_admin=auth.is_admin,
            db=db,
            storage=storage,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


router = APIRouter()
router.include_router(skill_patch_router)
