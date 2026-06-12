# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""审计日志管理后台 schema。

不与 plugin.py 混放，按职责拆分独立文件。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class AuditLogListItem(BaseModel):
    """列表项：不返回 detail/user_agent 大字段，减少传输。

    asset_display_name / asset_plugin_type 由后端 JOIN market_assets 补全：
    仅当 resource_id 是已知 asset_id 时有值，否则为 None（如 git_source 类记录）。
    """

    model_config = ConfigDict(exclude_none=True)

    event_id: str
    event_type: str
    action: str
    operator_id: str
    operator_name: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    resource_version: Optional[str] = None
    result: str
    ip_address: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    created_at_ms: int
    duration_ms: int = 0
    # 调用入口 (web/cli/api/background)，从 extra.source_channel 提升而来
    source_channel: Optional[str] = None
    asset_display_name: Optional[str] = None
    asset_plugin_type: Optional[str] = None
    asset_name: Optional[str] = None


class AuditLogDetail(AuditLogListItem):
    """单条详情：返回完整字段，含 detail / user_agent。"""

    detail: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None


class AuditLogListData(BaseModel):
    model_config = ConfigDict(exclude_none=True)

    items: List[AuditLogListItem]
    total: int
    page: int
    page_size: int


class AuditStatsData(BaseModel):
    model_config = ConfigDict(exclude_none=True)

    # 统计窗口由请求 query params (date_from_ms / date_to_ms) 决定，
    # 与列表/导出口径一致。前端通过副标题展示当前窗口。
    total: int
    failed: int
    slow: int
    # 慢操作阈值（毫秒），前端展示在卡片副标题里
    slow_threshold_ms: int
