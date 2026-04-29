from dataclasses import dataclass
from typing import Dict, Literal, List, Optional

from fastapi import UploadFile
from pydantic import BaseModel, Field, field_validator


@dataclass
class PluginPublishForm:
    file: UploadFile
    checksum: str
    plugin_id: Optional[str]
    plugin_version: Optional[str]
    version_desc: Optional[str]
    force: bool


class AssetCreate(BaseModel):
    """Parameters for creating a market asset."""

    asset_id: str
    name: str
    display_name: str
    asset_type: str = "plugin"
    short_desc: Optional[str] = None
    detail_desc: Optional[str] = None
    tags: Optional[List[str]] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    publisher_id: str = ""
    publisher_name: str = ""
    plugin_type: Optional[str] = None
    latest_version: Optional[str] = None


class AssetVersionCreate(BaseModel):
    """Parameters for creating an asset version."""

    version_id: str
    asset_id: str
    version: str
    changelog: Optional[str] = None
    status: str = "ACTIVE"
    file_path: Optional[str] = None
    artifact_sha256: Optional[str] = None


class PluginPublishResult(BaseModel):
    plugin_id: str
    name: str
    version: str
    status: str
    published_at: str
    storage_url: str
    plugin_type: Optional[str] = None


@dataclass
class SkillImportBundle:
    """POST /plugins/skill-import 多部分请求：上传文件、校验和头、表单选项。"""

    file: UploadFile
    checksum: str
    force: bool
    fail_fast: bool


class SkillImportItemResult(BaseModel):
    """单条 skill 导入结果。"""

    entry: str
    status: Literal["ok", "error"]
    plugin_id: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None


class SkillImportSummary(BaseModel):
    """汇总：``total`` 为集合包顶层 skill 目录数；``fail_fast`` 提前结束时 ``ok + failed`` 可能小于 ``total``。"""

    total: int = Field(..., description="集合包内顶层 skill 目录总数")
    ok: int = Field(..., description="成功导入条数")
    failed: int = Field(..., description="失败条数（仅含已尝试并记入 results 的条目）")


class SkillImportResponse(BaseModel):
    summary: SkillImportSummary
    results: list[SkillImportItemResult]


# ----- DELETE /api/v1/plugins/{asset_id}/versions/{version} -----


class PluginVersionDeleteData(BaseModel):
    asset_id: str
    version: str
    plugin_type: Optional[str] = None


class PluginTemplatePresignData(BaseModel):
    """GET /plugins/publish-template 返回的预签名下载信息。"""

    download_url: str
    expires_in: int
    filename: str


class PluginVersionDetail(BaseModel):
    asset_id: str
    version: str
    asset_type: str
    plugin_type: Optional[str] = None
    moderation_status: Optional[str] = Field(
        None,
        description="Skill 审核状态：PENDING | APPROVED | REJECTED；非 skill 多为 APPROVED",
    )
    moderation_reject_reason: Optional[str] = Field(None, description="审核不通过原因")
    version_moderation_status: Optional[str] = Field(
        None, description="当前版本的审核状态；Skill：PENDING | APPROVED | REJECTED"
    )
    version_moderation_reject_reason: Optional[str] = Field(
        None, description="当前版本审核驳回原因"
    )
    name: str
    display_name: str
    short_desc: Optional[str] = None
    detail_desc: Optional[str] = None
    publisher_id: str
    publisher_name: str
    tags: Optional[List[str]] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    certification: Optional[str] = None
    changelog: Optional[str] = None
    file_path: Optional[str] = None
    icon_uri: Optional[str] = None
    install_count: int = Field(
        0,
        description="与列表一致：资产累计下载次数（artifact 预签名下载成功时递增）",
    )
    view_count: int = Field(
        0,
        description="与列表一致：资产累计浏览次数（GET 版本详情成功返回前递增）",
    )
    update_time: Optional[int] = Field(
        None,
        description="当前查看的版本记录上传时间（market_asset_versions.create_time，毫秒）",
    )
    viewer_is_market_moderation_admin: bool = Field(
        False,
        description="当前请求者是否为市场审核管理员（与配置文件 / 系统 token 一致）",
    )


# ----- GET /api/v1/plugins 列表 -----


class PluginDownloadData(BaseModel):
    """GET /api/v1/artifacts/{id} 响应体 data。"""

    download_url: str
    asset_id: str
    name: str
    version: str
    file_size: int
    checksum_sha256: str


PLUGIN_ORDER_BY_OPTIONS = ("install_count", "like_count", "create_time", "update_time", "review_count")


OrderByField = Literal["install_count", "like_count", "create_time", "update_time", "review_count"]


class PluginListQuery(BaseModel):
    """GET /api/v1/plugins 的 query 参数（非必填）。"""

    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=200, description="每页条数")
    asset_id: Optional[str] = Field(None, description="资产 ID")
    asset_type: Optional[str] = Field(None, description="资产类型")
    publisher_id: Optional[str] = Field(None, description="发布者 ID")
    publisher_name: Optional[str] = Field(None, description="发布者名称（模糊）")
    category_id: Optional[str] = Field(None, description="分类 ID（精确匹配）")
    plugin_type: Optional[str] = Field(None, description="插件类型（精确匹配）")
    plugin_type_exclude: Optional[str] = Field(
        None,
        description='排除某 plugin_type（如 "skill"）：结果包含 plugin_type 为空或与该值不等的记录',
    )
    search_keyword: Optional[str] = Field(
        None, description="关键词（对 name/display_name/short_desc/detail_desc 模糊）"
    )
    moderation_status: Optional[str] = Field(
        None,
        description="按 Skill 审核状态筛选：PENDING | APPROVED | REJECTED；常配合 plugin_type=skill",
    )
    order_by: str = Field(
        "install_count",
        description="排序字段: install_count, like_count, create_time, update_time, review_count",
    )
    desc: bool = Field(True, description="排序方向: true=降序, false=升序")  # True=降序，False=升序

    @field_validator("order_by", mode="before")
    @classmethod
    def normalize_order_by(cls, v: object) -> str:
        if v is None:
            return "install_count"
        s = str(v).strip()
        if not s:
            raise ValueError("order_by cannot be empty")
        if s in PLUGIN_ORDER_BY_OPTIONS:
            return s
        allowed = ", ".join(PLUGIN_ORDER_BY_OPTIONS)
        raise ValueError(f"order_by must be one of: {allowed}; got {v!r}")

    @field_validator("moderation_status", mode="before")
    @classmethod
    def normalize_moderation_status(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().upper()
        if not s:
            return None
        if s in ("PENDING", "APPROVED", "REJECTED"):
            return s
        raise ValueError("moderation_status must be one of: PENDING, APPROVED, REJECTED")


class SkillModerationRequest(BaseModel):
    """POST /plugins/{asset_id}/moderation 请求体。"""

    action: Literal["approve", "reject"]
    reason: Optional[str] = Field(None, description="action=reject 时必填")
    version: Optional[str] = Field(
        None,
        description="要审核的版本号，缺省为资产当前 latest_version",
    )


class SkillModerationResult(BaseModel):
    asset_id: str
    moderation_status: str
    moderation_reject_reason: Optional[str] = None
    version: Optional[str] = Field(None, description="本次操作针对的版本号")


class SkillModerationAuditListItem(BaseModel):
    """审核员个人审核历史列表项（来自 audit_logs，event_type=SKILL_MODERATION）。"""

    event_id: str
    asset_id: str
    skill_name: str = Field(..., description="Skill 标识 name")
    skill_display_name: Optional[str] = None
    version: str
    moderation_action: Literal["APPROVE", "REJECT"] = Field(
        ...,
        description="本次审核操作：通过或驳回",
    )
    reject_reason: Optional[str] = Field(None, description="驳回原因；通过时为空")
    created_at_ms: int = Field(..., description="操作时间（毫秒时间戳，东八区写入）")


class SkillModerationAuditListResponse(BaseModel):
    """GET /plugins/audit/skill-moderation 响应 data。"""

    page: int
    page_size: int
    total: int
    items: list[SkillModerationAuditListItem]


class PluginListItem(BaseModel):
    """列表项，不包含 status。"""

    asset_id: str
    asset_type: str
    name: str
    display_name: Optional[str] = None
    short_desc: Optional[str] = None
    detail_desc: Optional[str] = None
    icon_uri: Optional[str] = None
    publisher_id: str
    publisher_name: str
    tags: Optional[List[str]] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    certification: Optional[str] = None
    plugin_type: Optional[str] = None
    moderation_status: Optional[str] = Field(None, description="Skill：PENDING | APPROVED | REJECTED")
    moderation_reject_reason: Optional[str] = None
    latest_version: Optional[str] = None
    public_latest_version: Optional[str] = Field(
        None,
        description="当前对外可下载/展示的已通过审最新版本；非发布者/非审核员列表与下载以此为准",
    )
    all_versions: List[str] = Field(
        default_factory=list,
        description="对当前用户可见的版本号：他人仅含已通过审版本；作者与审核员可见全部",
    )
    has_pending_skill_version: bool = Field(
        False,
        description="Skill：作者或审核员可见；仍有任一版本在审核中时为 true，用于个人中心「新版本审核中」",
    )
    skill_version_moderation: Optional[Dict[str, str]] = Field(
        None,
        description="Skill：仅发布者或审核员；version -> PENDING|APPROVED|REJECTED，用于详情/个人中心版本下拉展示",
    )
    view_count: int = 0
    install_count: int = 0
    like_count: int = 0
    star_count: int = 0
    review_count: int = 0
    average_rating: float = 8.0
    create_time: Optional[int] = None
    update_time: Optional[int] = None
    pin_order: Optional[int] = Field(
        None,
        description="置顶顺序：非空表示置顶，数字越小越靠前；为空则按 order_by 排序",
    )
    viewer_is_market_moderation_admin: bool = Field(
        False,
        description="当前请求者是否为市场审核管理员",
    )

    model_config = {"from_attributes": True}


class PluginListResponse(BaseModel):
    """GET /api/v1/plugins 响应体 data。"""

    page: int
    page_size: int
    total: int
    items: list[PluginListItem]
