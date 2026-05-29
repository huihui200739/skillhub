# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import hashlib
import logging
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path as FsPath
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from common.security.security_utils import SecurityUtils
from plugins_market.core.audit import audit_log
from plugins_market.core.audit_events import Action, EventType, ResourceType, Result
from plugins_market.core.moderation import is_skill_like_plugin_type
from plugins_market.core.auth import (
    AuthContext,
    get_oauth_user_id_and_login,
    normalize_oauth_provider_header,
    require_auth,
    resolve_viewer_context,
)
from plugins_market.core.context import (
    set_audit_hint,
    set_user_id,
    set_user_name,
    get_user_id as get_user_id_from_context,
    get_user_name,
)
from plugins_market.core.viewer_context import ViewerContext

logger = logging.getLogger(__name__)
from plugins_market.core.config import settings
from plugins_market.core.database import get_db
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.repositories.git_source_repository import GitSourceRepository
from plugins_market.validation.constants import MAX_FILE_SIZE, ZIP_STREAM_READ_CHUNK_BYTES
from plugins_market.imports.skill_import_service import skill_import_from_bundle
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.plugin import (
    GitSourceCreateRequest,
    GitSourceItem,
    GitSourceListResponse,
    GitSyncAcceptedResponse,
    PluginDownloadData,
    PluginListItem,
    PluginListQuery,
    PluginListResponse,
    PluginPublishForm,
    PluginPublishResult,
    PluginTemplatePresignData,
    PluginVersionDeleteData,
    PluginVersionDetail,
    SkillImportBundle,
    SkillImportResponse,
    SkillModerationRequest,
    SkillModerationResult,
    SkillModerationAuditListResponse,
    VersionFilesData,
)
from plugins_market.services import (
    PublishError,
    delete_plugin_version_service,
    get_plugin_version_detail_service,
    get_version_file_list_service,
    list_my_skill_moderation_audits_service,
    list_plugins_service,
    get_download_info,
    moderate_skill_asset_service,
    publish as plugin_publish,
)
from plugins_market.services.git_skill_sync import (
    create_git_source,
    delete_git_source_for_user,
    mark_git_source_syncing,
    prepare_git_source_sync_start,
    recover_stale_git_sources_for_user,
    run_git_source_sync_background,
    unregister_local_git_sync,
)
from plugins_market.services.skill_review import schedule_skill_publish_review
from plugins_market.core.publish_result import (
    PUBLISH_RESULT_PENDING_MODERATION,
    PUBLISH_RESULT_REVIEWING,
)

plugin_router = APIRouter(prefix="/plugins", tags=["plugins"])
artifact_router = APIRouter(prefix="/artifacts", tags=["plugins"])

_skill_import_req_times: deque[float] = deque()
_skill_import_rl_lock = asyncio.Lock()
_git_sync_req_times_by_user: dict[str, deque[float]] = {}
_git_sync_rl_lock = asyncio.Lock()
_git_sync_rl_op_count = 0
_GIT_SYNC_RL_PRUNE_ALL_EVERY = 128


async def _enforce_skill_import_rate_limit() -> None:
    limit = settings.skill_import_rate_limit_per_minute
    if limit <= 0:
        return
    async with _skill_import_rl_lock:
        now = time.monotonic()
        window = 60.0
        while _skill_import_req_times and _skill_import_req_times[0] < now - window:
            _skill_import_req_times.popleft()
        if len(_skill_import_req_times) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": status.HTTP_429_TOO_MANY_REQUESTS,
                    "data": None,
                    "error": "rate_limited",
                    "message": "skill-import 请求过于频繁，请稍后再试",
                },
            ) from None
        _skill_import_req_times.append(now)


def _prune_git_sync_rate_limit_buckets(*, now: float, window: float) -> None:
    """移除窗口外时间戳；删除空 deque，避免按用户 key 无限累积。"""
    for uid in list(_git_sync_req_times_by_user):
        bucket = _git_sync_req_times_by_user[uid]
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if not bucket:
            del _git_sync_req_times_by_user[uid]


async def _enforce_git_source_sync_rate_limit(user_id: str) -> None:
    limit = settings.git_source_sync_rate_limit_per_minute
    if limit <= 0:
        return
    uid = (user_id or "").strip() or "_anonymous"
    global _git_sync_rl_op_count
    async with _git_sync_rl_lock:
        now = time.monotonic()
        window = 60.0
        bucket = _git_sync_req_times_by_user.get(uid)
        if bucket is None:
            bucket = deque()
            _git_sync_req_times_by_user[uid] = bucket
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": status.HTTP_429_TOO_MANY_REQUESTS,
                    "data": None,
                    "error": "rate_limited",
                    "message": "Git 源同步请求过于频繁，请稍后再试",
                },
            ) from None
        bucket.append(now)
        _git_sync_rl_op_count += 1
        if _git_sync_rl_op_count % _GIT_SYNC_RL_PRUNE_ALL_EVERY == 0:
            _prune_git_sync_rate_limit_buckets(now=now, window=window)


def _auth_error(status_code: int, message: str, *, error: str = "permission_denied") -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": status_code,
            "data": None,
            "error": error,
            "message": message,
        },
    )


def _parse_form_bool(value: Optional[str]) -> bool:
    if not value:
        return False
    return str(value).strip().lower() in ("true", "1", "on")


def _parse_fail_fast_query(
    fail_fast: Optional[str] = Query(
        None,
        description="遇首条失败即停止：true/1/on 为开启；未传或其它任意值视为关闭（避免无法解析为布尔时整请求 422）",
    ),
) -> bool:
    """与 multipart 的 fail_fast 表单语义一致；非法查询值视为 false，不把整段 POST 判为 422。"""
    return _parse_form_bool(fail_fast)


def valid_checksum(
    checksum: str = Header(..., alias="X-Checksum-SHA256"),
) -> str:
    value = checksum.strip().lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise HTTPException(
            status_code=400,
            detail={
                "code": 400,
                "data": None,
                "error": "checksum_required",
                "message": "请求头 X-Checksum-SHA256 必填，且为 64 位小写十六进制字符串",
            },
        )
    return value


async def build_skill_import_bundle(
    file: UploadFile = File(..., description="技能集合包（ZIP，顶层为多个 skill 目录）"),
    checksum: str = Depends(valid_checksum),
    force: str = Form("false"),
    fail_fast: str = Form("false"),
) -> SkillImportBundle:
    return SkillImportBundle(
        file=file,
        checksum=checksum,
        force=_parse_form_bool(force),
        fail_fast=_parse_form_bool(fail_fast),
    )


class PublishFormRequired:
    """必填表单参数"""

    def __init__(
        self,
        file: UploadFile = File(..., description="插件包文件（.zip 格式）"),
        checksum: str = Depends(valid_checksum),
    ):
        self.file = file
        self.checksum = checksum


class PublishFormOptional:
    """可选表单参数"""

    def __init__(
        self,
        plugin_id: Optional[str] = Form(
            None,
            description="已存在插件发新版本时必填；首次发布请勿填写，由系统生成 plugin_id",
        ),
        plugin_version: Optional[str] = Form(None),
        version_desc: Optional[str] = Form(None),
        force: bool = Form(False),
    ):
        self.plugin_id = plugin_id.strip() if plugin_id else None
        self.plugin_version = plugin_version.strip() if plugin_version else None
        self.version_desc = version_desc.strip() if version_desc else None
        self.force = force


async def build_publish_form(
    required: PublishFormRequired = Depends(),
    optional: PublishFormOptional = Depends(),
) -> PluginPublishForm:
    """
    必须是 async：set_audit_hint() 改 ContextVar，FastAPI 把 sync 依赖丢 threadpool
    跑会导致 ContextVar 修改在线程副本里丢失，主任务 / exception handler 拿不到。
    """
    form = PluginPublishForm(
        file=required.file,
        checksum=required.checksum,
        plugin_id=optional.plugin_id,
        plugin_version=optional.plugin_version,
        version_desc=optional.version_desc,
        force=optional.force,
    )
    # 失败补录提示：发布失败时可从这里取 skill 名/版本/文件名（业务代码无感）。
    # 新建发布时 plugin_id 可能为空（名字从 zip manifest 推断），那时仍能拿到 upload_filename
    upload_filename = form.file.filename if form.file else None
    set_audit_hint(
        skill_name=form.plugin_id,
        resource_id=form.plugin_id,
        resource_version=form.plugin_version,
        upload_filename=upload_filename,
    )
    return form


@dataclass(frozen=True)
class _ServiceDeps:
    db: Session
    storage: Any
    viewer: ViewerContext


def _get_service_deps(
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
) -> "_ServiceDeps":
    return _ServiceDeps(db=db, storage=storage, viewer=viewer)


@dataclass(frozen=True)
class PublishPluginDependencies:
    db: Session
    storage: Any


def get_publish_plugin_dependencies(
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
) -> PublishPluginDependencies:
    return PublishPluginDependencies(db=db, storage=storage)


@dataclass(frozen=True)
class GitSourceSyncRouteDeps:
    request: Request
    background_tasks: BackgroundTasks
    auth: AuthContext
    db: Session
    fail_fast: bool


def get_git_source_sync_route_deps(
    request: Request,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    fail_fast: bool = Depends(_parse_fail_fast_query),
) -> GitSourceSyncRouteDeps:
    return GitSourceSyncRouteDeps(
        request=request,
        background_tasks=background_tasks,
        auth=auth,
        db=db,
        fail_fast=fail_fast,
    )


def get_publish_auth(
    authorization: Optional[str] = Header(None, description="Authorization: Bearer <token>"),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
    x_oauth_provider: Optional[str] = Header(None, alias="X-OAuth-Provider"),
) -> Tuple[Optional[str], bool, Optional[str], str]:
    """
    返回 (token, is_system_token, acting_user_id, oauth_provider)
    - is_system_token=True：表示通过 X-System-Token（oauth_provider 占位为 gitcode，不使用）
    - is_system_token=False：token 需结合 oauth_provider 调用厂商用户接口鉴权
    """
    has_auth = bool(authorization and authorization.strip().lower().startswith("bearer "))
    has_bearer_token = has_auth
    has_system = bool(x_system_token and x_system_token.strip())

    auth_count = int(has_system) + int(has_bearer_token)
    if auth_count != 1:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "Missing/invalid authorization: provide exactly one of Authorization: Bearer <token>, or X-System-Token",
        )

    if has_system:
        system_admin_token = SecurityUtils.get_decrypt_secret("SYSTEM_ADMIN_TOKEN", default="") or ""
        if system_admin_token and x_system_token.strip() == system_admin_token:
            acting = settings.system_admin_user
            return (None, True, acting, "gitcode")
        raise _auth_error(status.HTTP_401_UNAUTHORIZED, "Invalid X-System-Token")

    token = authorization[7:].strip()
    if not token:
        raise _auth_error(status.HTTP_401_UNAUTHORIZED, "Invalid or empty token")
    try:
        oauth_provider = normalize_oauth_provider_header(x_oauth_provider)
    except HTTPException as e:
        raise _auth_error(
            status.HTTP_400_BAD_REQUEST,
            str(e.detail) if isinstance(e.detail, str) else "Invalid X-OAuth-Provider",
            error="invalid_oauth_provider",
        ) from e
    return (token, False, None, oauth_provider)


@plugin_router.post("", response_model=ResponseModel[PluginPublishResult])
async def publish_plugin(
    request: Request,
    background_tasks: BackgroundTasks,
    form: PluginPublishForm = Depends(build_publish_form),
    dependencies: PublishPluginDependencies = Depends(get_publish_plugin_dependencies),
    auth: Tuple[Optional[str], bool, Optional[str], str] = Depends(get_publish_auth),
):
    db = dependencies.db
    storage = dependencies.storage
    token, is_system_token, acting_user_id, oauth_provider = auth
    publisher_name_override: str | None = None
    if not is_system_token:
        acting_user_id, publisher_name_override = await get_oauth_user_id_and_login(
            token or "",
            oauth_provider,
        )
    else:
        publisher_name_override = settings.system_admin_user
    set_user_id(acting_user_id or "")
    set_user_name(publisher_name_override)  # 失败补录的 operator_name 来源

    content = await form.file.read()
    try:
        result = plugin_publish(
            user_id=acting_user_id or "",
            content=content,
            filename=form.file.filename,
            expected_checksum=form.checksum,
            plugin_id=form.plugin_id,
            plugin_version=form.plugin_version,
            version_desc=form.version_desc,
            force=form.force,
            db=db,
            storage=storage,
            publisher_name_override=publisher_name_override,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    if result.publish_result == PUBLISH_RESULT_REVIEWING:
        background_tasks.add_task(schedule_skill_publish_review, result.plugin_id, result.version, "api_background")

    is_skill_like = is_skill_like_plugin_type(result.plugin_type)
    event_type = EventType.SKILL_MANAGE if is_skill_like else EventType.PLUGIN_MANAGE
    resource_type = ResourceType.SKILL if is_skill_like else ResourceType.PLUGIN
    audit_log(
        event_type=event_type,
        action=Action.PUBLISH,
        operator_id=acting_user_id or "",
        operator_name=publisher_name_override,
        resource_type=resource_type,
        resource_id=result.plugin_id if hasattr(result, "plugin_id") else str(result),
        resource_version=result.version if hasattr(result, "version") else None,
        detail=f"发布{resource_type}成功: {getattr(result, 'plugin_id', '')} v{getattr(result, 'version', '')}",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        extra={
            "force": form.force,
            "skill_name": getattr(result, "name", None),
            "skill_display_name": getattr(result, "display_name", None),
        },
    )

    return ResponseModel(
        code=status.HTTP_200_OK,
        message=(
            "Skill 已提交，正在自动审查"
            if result.publish_result == PUBLISH_RESULT_REVIEWING
            else (
                "Skill 已提交，等待人工审核"
                if result.publish_result == PUBLISH_RESULT_PENDING_MODERATION
                else "Publish plugin successfully"
            )
        ),
        data=result,
    )


def _template_filename_from_key(key: str) -> str:
    base = (key or "").strip().rstrip("/").split("/")[-1]
    return base or "plugin-template.zip"


@plugin_router.get(
    "/publish-template",
    response_model=ResponseModel[PluginTemplatePresignData],
)
async def get_publish_template_presigned(
    auth: AuthContext = Depends(require_auth),
    storage=Depends(get_storage_client),
    kind: Optional[str] = Query(
        None,
        description='模板种类：不传或 "plugin" 为插件模板；传 "skill" 为 Skill 模板',
    ),
):
    """为发布页「下载模板」生成私有桶对象的预签名 GET URL（需 Bearer 或 X-System-Token）。"""
    _ = auth
    use_skill = is_skill_like_plugin_type(kind)
    if use_skill:
        key = (settings.skill_template_object_key or "").strip()
        unset_msg = "未配置 Skill 发布模板对象路径（MARKET_SKILL_TEMPLATE_OBJECT_KEY）"
    else:
        key = (settings.plugin_template_object_key or "").strip()
        unset_msg = "未配置发布模板对象路径（MARKET_PLUGIN_TEMPLATE_OBJECT_KEY）"
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": 503,
                "data": None,
                "error": "template_not_configured",
                "message": unset_msg,
            },
        )
    try:
        url = storage.presigned_get_url(key)
        ttl = storage.config.presigned_expires_seconds
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": 500,
                "data": None,
                "error": "presign_failed",
                "message": f"生成模板下载链接失败：{e!s}",
            },
        ) from e

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=PluginTemplatePresignData(
            download_url=url,
            expires_in=int(ttl),
            filename=_template_filename_from_key(key),
        ),
    )


@plugin_router.post(
    "/skill-import",
    response_model=ResponseModel[SkillImportResponse],
)
async def skill_import(
    request: Request,
    bundle: SkillImportBundle = Depends(build_skill_import_bundle),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    auth: Tuple[Optional[str], bool, Optional[str], str] = Depends(get_publish_auth),
):
    """批量导入 skill：仅 X-System-Token；须 X-Checksum-SHA256。"""
    await _enforce_skill_import_rate_limit()

    _token, is_system_token, acting_user_id, _oauth_provider = auth
    if not is_system_token:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "批量导入仅支持 X-System-Token（系统管理员）",
            error="forbidden",
        )
    set_user_id(acting_user_id or "")

    tmp_path: FsPath | None = None
    upload_tmp_name: str | None = None
    try:
        # NamedTemporaryFile + with：退出 with 时文件对象关闭（G.FIO.04，无裸 fd）
        with tempfile.NamedTemporaryFile(
            prefix="oj_skill_bundle_",
            suffix=".zip",
            delete=False,
            mode="wb",
        ) as out:
            upload_tmp_name = out.name
            tmp_path = FsPath(out.name)
            hasher = hashlib.sha256()
            written = 0
            while True:
                chunk = await bundle.file.read(ZIP_STREAM_READ_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": 400,
                            "data": None,
                            "error": "payload_too_large",
                            "message": "技能集合包原始大小超过 512MB 上限",
                        },
                    ) from None
                hasher.update(chunk)
                out.write(chunk)

        if hasher.hexdigest() != bundle.checksum:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": 400,
                    "data": None,
                    "error": "checksum_mismatch",
                    "message": "技能集合包 X-Checksum-SHA256 与实际上传内容不一致",
                },
            ) from None

        try:
            data = skill_import_from_bundle(
                bundle_path=tmp_path,
                user_id=acting_user_id or "",
                db=db,
                storage=storage,
                force=bundle.force,
                fail_fast=bundle.fail_fast,
            )
        except PublishError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail) from e

        # 失败明细：按 error/skipped 拆开，便于事后排查
        failed_items = [
            {
                "entry": item.entry,
                "name": item.name,
                "version": item.version,
                "error": item.error,
                "message": item.message,
            }
            for item in (data.results or [])
            if item.status == "error"
        ]
        skipped_items = [
            {
                "entry": item.entry,
                "name": item.name,
                "version": item.version,
                "message": item.message,
            }
            for item in (data.results or [])
            if item.status == "skipped"
        ]
        # 整体 result：只要有失败就算 PARTIAL_FAILED，全部失败算 FAILED
        if data.summary.failed > 0 and data.summary.ok == 0:
            import_result = Result.FAILED
        elif data.summary.failed > 0:
            import_result = Result.PARTIAL_FAILED
        else:
            import_result = Result.SUCCESS
        audit_log(
            event_type=EventType.SKILL_MANAGE,
            action=Action.IMPORT,
            operator_id=acting_user_id or "",
            operator_name=settings.system_admin_user,
            resource_type=ResourceType.SKILL_BUNDLE,
            result=import_result,
            detail=(
                f"批量导入 Skill 完成，成功 {data.summary.ok} 个，"
                f"失败 {data.summary.failed} 个，跳过 {data.summary.skipped} 个，"
                f"共 {data.summary.total} 个"
            ),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            extra={
                "force": bundle.force,
                "fail_fast": bundle.fail_fast,
                "total": data.summary.total,
                "ok_count": data.summary.ok,
                "failed_count": data.summary.failed,
                "skipped_count": data.summary.skipped,
                # 控制 extra 大小：失败 / 跳过明细各留前 50 条，truncated 标志记录是否截断
                "failed_items": failed_items[:50],
                "failed_items_truncated": len(failed_items) > 50,
                "skipped_items": skipped_items[:50],
                "skipped_items_truncated": len(skipped_items) > 50,
            },
        )

        return ResponseModel(
            code=status.HTTP_200_OK,
            message="Import skills finished",
            data=data,
        )
    finally:
        if upload_tmp_name:
            try:
                FsPath(upload_tmp_name).unlink(missing_ok=True)
            except OSError:
                pass


@plugin_router.get(
    "/git-sources",
    response_model=ResponseModel[GitSourceListResponse],
)
async def list_my_git_sources(
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """当前用户注册的 Git 仓库源列表。"""
    _ = set_user_id(auth.acting_user_id)
    recover_stale_git_sources_for_user(db, auth.acting_user_id)
    rows = GitSourceRepository(db).list_by_user(auth.acting_user_id)
    items = [GitSourceItem.model_validate(r) for r in rows]
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=GitSourceListResponse(items=items))


@plugin_router.post(
    "/git-sources",
    response_model=ResponseModel[GitSyncAcceptedResponse],
)
async def create_git_source_and_sync_route(
    body: GitSourceCreateRequest,
    deps: GitSourceSyncRouteDeps = Depends(get_git_source_sync_route_deps),
):
    auth = deps.auth
    await _enforce_git_source_sync_rate_limit(auth.acting_user_id)
    set_user_id(auth.acting_user_id)
    try:
        src = create_git_source(
            db=deps.db,
            user_id=auth.acting_user_id,
            name=body.name,
            repo_url=body.repo_url,
            ref=body.ref,
            skills_subpath=body.skills_subpath,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    try:
        prepare_git_source_sync_start(deps.db, src)
        mark_git_source_syncing(deps.db, src)
        deps.background_tasks.add_task(
            run_git_source_sync_background,
            source_id=src.id,
            user_id=auth.acting_user_id,
            fail_fast=deps.fail_fast,
        )
    except PublishError as e:
        unregister_local_git_sync(src.id)
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except Exception:
        unregister_local_git_sync(src.id)
        raise

    audit_log(
        event_type=EventType.SKILL_MANAGE,
        action=Action.GIT_SYNC,
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type=ResourceType.GIT_SOURCE,
        resource_id=src.id,
        detail=f"创建 Git 源（后台同步）: {src.name} {src.repo_url}",
        ip_address=deps.request.client.host if deps.request.client else None,
        user_agent=deps.request.headers.get("user-agent"),
        extra={
            "git_source_name": src.name,
            "repo_url": src.repo_url,
            "git_action": "create",
        },
    )
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=GitSyncAcceptedResponse(source_id=src.id),
    )


@plugin_router.post(
    "/git-sources/{source_id}/sync",
    response_model=ResponseModel[GitSyncAcceptedResponse],
)
async def sync_git_source_route(
    source_id: str,
    deps: GitSourceSyncRouteDeps = Depends(get_git_source_sync_route_deps),
):
    auth = deps.auth
    await _enforce_git_source_sync_rate_limit(auth.acting_user_id)
    set_user_id(auth.acting_user_id)
    gs_repo = GitSourceRepository(deps.db)
    src = gs_repo.get_by_id(source_id)
    if src is None or src.created_by_user_id != auth.acting_user_id:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "无权同步该 Git 源或资源不存在",
            error="forbidden",
        )
    try:
        prepare_git_source_sync_start(deps.db, src)
        mark_git_source_syncing(deps.db, src)
        deps.background_tasks.add_task(
            run_git_source_sync_background,
            source_id=src.id,
            user_id=auth.acting_user_id,
            fail_fast=deps.fail_fast,
        )
    except PublishError as e:
        unregister_local_git_sync(src.id)
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except Exception:
        unregister_local_git_sync(src.id)
        raise

    audit_log(
        event_type=EventType.SKILL_MANAGE,
        action=Action.GIT_SYNC,
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type=ResourceType.GIT_SOURCE,
        resource_id=src.id,
        detail=f"同步 Git 源（后台）: {src.name}",
        ip_address=deps.request.client.host if deps.request.client else None,
        user_agent=deps.request.headers.get("user-agent"),
        extra={
            "git_source_name": src.name,
            "repo_url": src.repo_url,
            "git_action": "sync",
        },
    )
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=GitSyncAcceptedResponse(source_id=src.id),
    )


@plugin_router.delete(
    "/git-sources/{source_id}",
    response_model=ResponseModel[dict],
)
async def delete_git_source_route(
    request: Request,
    source_id: str,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    set_user_id(auth.acting_user_id)
    # 删除前抓拍 git 源元数据，否则审计写时已经查不到 name / repo_url
    gs_repo = GitSourceRepository(db)
    snapshot = gs_repo.get_by_id(source_id)
    snapshot_name = snapshot.name if snapshot else None
    snapshot_repo_url = snapshot.repo_url if snapshot else None
    try:
        delete_git_source_for_user(
            db=db,
            user_id=auth.acting_user_id,
            source_id=source_id,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    audit_log(
        event_type=EventType.SKILL_MANAGE,
        action=Action.GIT_SOURCE_DELETE,
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type=ResourceType.GIT_SOURCE,
        resource_id=source_id,
        detail=(
            f"删除 Git 源: {snapshot_name} ({snapshot_repo_url})"
            if snapshot_name
            else "删除 Git 源注册"
        ),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        extra={
            "git_source_name": snapshot_name,
            "repo_url": snapshot_repo_url,
            "git_action": "delete",
        },
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"deleted": True})


@plugin_router.get(
    "",
    response_model=ResponseModel[PluginListResponse],
)
async def list_plugins(
    query: PluginListQuery = Depends(),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
):
    data = list_plugins_service(query=query, db=db, storage=storage, viewer=viewer)
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.get(
    "/audit/skill-moderation",
    response_model=ResponseModel[SkillModerationAuditListResponse],
)
async def list_my_skill_moderation_audits(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
):
    """审核管理员：本人作为操作者产生的 Skill 审核审计记录，按时间倒序。"""
    data = list_my_skill_moderation_audits_service(
        auth=auth,
        db=db,
        page=page,
        page_size=page_size,
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@artifact_router.get(
    "/{id}",
    response_model=ResponseModel[PluginDownloadData],
)
async def get_artifact_download(
    request: Request,
    artifact_id: str = Path(..., alias="id"),
    version: Optional[str] = Query(None, description="版本号（如 1.0.0），不指定则返回最新版本"),
    is_cli_download: bool = Query(False, description="是否 CLI 下载；CLI=true 下载原始 zip，其他下载 raw.zip"),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
):
    fetch_user_id: Optional[str] = get_user_id_from_context()

    try:
        result = get_download_info(
            asset_id=artifact_id,
            version=version,
            db=db,
            storage=storage,
            fetch_user_id=fetch_user_id,
            viewer=viewer,
            is_cli_download=is_cli_download,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    # 审计：包出系统，敏感数据出口必须留痕。
    # resource_type 按资产真实 plugin_type 记录（skill / swarmskill / plugin），不再写死 skill，
    # 否则插件下载会被错记成 skill，污染 resource_type 过滤与"对象类型"列展示。
    # 失败下载（404/403）由 GET 路径不在 audit_failed 范围内，暂不补录——
    # 若未来需要追踪未授权访问尝试，可在此 except 分支前加一条 FAILED 审计。
    download_resource_type = {
        ResourceType.SKILL: ResourceType.SKILL,
        ResourceType.SWARMSKILL: ResourceType.SWARMSKILL,
        ResourceType.PLUGIN: ResourceType.PLUGIN,
    }.get((result.plugin_type or "").strip().lower(), ResourceType.SKILL)
    try:
        audit_log(
            event_type=EventType.SKILL_USE,
            action=Action.DOWNLOAD,
            operator_id=fetch_user_id or "anonymous",
            operator_name=get_user_name(),
            resource_type=download_resource_type,
            resource_id=result.asset_id,
            resource_version=result.version,
            result=Result.SUCCESS,
            detail=f"下载 {result.name} v{result.version}",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            extra={
                "skill_name": result.name,
                "plugin_type": result.plugin_type,
                "file_size": int(result.file_size),
                "checksum_sha256": result.checksum_sha256,
                "is_cli_download": bool(is_cli_download),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_log DOWNLOAD suppressed exception: %s", exc, exc_info=True)

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=result,
    )



@plugin_router.get(
    "/{asset_id}/versions/{version}/files",
    response_model=ResponseModel[VersionFilesData],
)
async def list_version_files(
    asset_id: str,
    version: str,
    with_content: Optional[str] = Query(None, description="同时返回该文件的文本内容"),
    deps: _ServiceDeps = Depends(_get_service_deps),
):
    """返回版本 zip 包内文件列表；传 with_content=<path> 可在同一请求内附带指定文件内容。"""
    data = get_version_file_list_service(
        asset_id=asset_id,
        version=version,
        db=deps.db,
        storage=deps.storage,
        viewer=deps.viewer,
        with_content=with_content,
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.get(
    "/{asset_id}/versions/{version}",
    response_model=ResponseModel[PluginVersionDetail],
)
async def get_plugin_version_detail(
    asset_id: str,
    version: str,
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
):
    data = get_plugin_version_detail_service(
        asset_id=asset_id,
        version=version,
        db=db,
        storage=storage,
        viewer=viewer,
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.post(
    "/{asset_id}/moderation",
    response_model=ResponseModel[SkillModerationResult],
)
async def moderate_skill(
    asset_id: str,
    body: SkillModerationRequest,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
):
    # 失败补录提示：审核失败时按本次意图记 APPROVE/REJECT，而非泛化的 MODERATE，
    # 让审计页「审核通过/驳回」筛选也能命中失败的审核尝试。非法 action 不设，保持 MODERATE 兜底。
    _moderate_action = {"approve": Action.APPROVE, "reject": Action.REJECT}.get(
        (body.action or "").strip().lower()
    )
    if _moderate_action:
        # 同时透传 body.version：失败审核也能记下"尝试审核的版本"（None 会被 set_audit_hint 忽略）
        set_audit_hint(action=_moderate_action, resource_id=asset_id, resource_version=body.version)
    try:
        data = moderate_skill_asset_service(
            asset_id=asset_id,
            action=body.action,
            reason=body.reason,
            version=body.version,
            auth=auth,
            db=db,
            storage=storage,
        )
    except PublishError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.delete(
    "/{asset_id}/versions/{version}",
    response_model=ResponseModel[PluginVersionDeleteData],
)
async def delete_plugin_version(
    asset_id: str,
    version: str,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    data = delete_plugin_version_service(
        asset_id=asset_id,
        version=version,
        auth=auth,
        db=db,
        storage=storage,
    )

    is_skill_like = is_skill_like_plugin_type(data.plugin_type)
    event_type = EventType.SKILL_MANAGE if is_skill_like else EventType.PLUGIN_MANAGE
    resource_type = ResourceType.SKILL if is_skill_like else ResourceType.PLUGIN
    # 删除后 asset 行已不存在，名称必须在 service 删除前抓拍并通过 data 透传
    skill_display_for_detail = (
        data.skill_display_name or data.skill_name or asset_id
    )
    audit_log(
        event_type=event_type,
        action=Action.DELETE,
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type=resource_type,
        resource_id=asset_id,
        resource_version=version,
        detail=f"删除{resource_type}「{skill_display_for_detail}」版本 {version}",
        ip_address=auth.ip_address,
        user_agent=auth.user_agent,
        extra={
            "deleted_all": version.lower() == "all",
            "skill_name": data.skill_name,
            "skill_display_name": data.skill_display_name,
        },
    )

    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


router = APIRouter()
router.include_router(plugin_router)
router.include_router(artifact_router)
