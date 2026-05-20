# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from pathlib import Path
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")

MARKETPLACE_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# 与 marketplace validation/constants.py 一致：Git commit 固定 7 位小写 hex
MARKETPLACE_GIT_COMMIT_VERSION_PATTERN = re.compile(r"^[0-9a-f]{7}$")
# 与 frontend formatSkillVersionLabel.ts 一致
_GIT_COMMIT_VERSION_LABEL = re.compile(r"^[0-9a-fA-F]{7}$")
_SEMVER_CORE_LABEL = re.compile(r"^(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$", re.IGNORECASE)


def format_skill_version_label(
    raw: str | None,
    *,
    git_version_display_as_commit: bool = False,
    resolved_commit_sha: str | None = None,
) -> str:
    """市场版本展示文案（与前端 ``formatSkillVersionLabel`` 对齐）。"""
    sha = (resolved_commit_sha or "").strip().lower()
    s0 = (raw or "").strip()
    stripped_v = re.sub(r"^v+", "", s0, flags=re.IGNORECASE)

    if git_version_display_as_commit and sha:
        return sha[:7]
    if not s0:
        return ""
    if _GIT_COMMIT_VERSION_LABEL.match(stripped_v):
        return stripped_v.lower()
    m = _SEMVER_CORE_LABEL.match(stripped_v)
    if m:
        return f"v{m.group(1)}"
    return s0


def format_market_skill_version_label(
    raw: str | None,
    *,
    latest_version: str | None,
    git_version_display_as_commit: bool = False,
    resolved_commit_sha: str | None = None,
) -> str:
    """列表项中单条版本展示（与前端 ``formatMarketSkillVersionLabel`` 对齐）。"""
    v = (raw or "").strip()
    if not v:
        return ""
    latest = (latest_version or "").strip()
    pass_commit = bool(git_version_display_as_commit) and v == latest
    return format_skill_version_label(
        v,
        git_version_display_as_commit=pass_commit,
        resolved_commit_sha=resolved_commit_sha,
    )


def is_valid_marketplace_version(value: str) -> bool:
    s = (value or "").strip()
    if not s:
        return False
    if MARKETPLACE_VERSION_PATTERN.match(s):
        return True
    return bool(MARKETPLACE_GIT_COMMIT_VERSION_PATTERN.match(s.lower()))


def normalize_marketplace_version_optional(value: str | None) -> str | None:
    """Normalize optional version: semver x.y.z (optional v prefix) or git commit 7 位 hex。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if MARKETPLACE_VERSION_PATTERN.match(s):
        return s
    low = s.lower()
    if MARKETPLACE_GIT_COMMIT_VERSION_PATTERN.match(low):
        return low
    if len(s) > 1 and s[0] in ("v", "V"):
        s = s[1:].strip()
    if not MARKETPLACE_VERSION_PATTERN.match(s):
        raise ValueError(
            "version must be marketplace semver x.y.z (e.g. 1.0.0) or a git commit "
            "(7 lowercase hex digits); optional leading v/V is accepted for semver only"
        )
    return s


# ---- Common wrapper --------------------------------------------------------
class ResponseModel(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="ignore")

    code: int = 200
    message: str = "Success"
    data: T | None = None


# ---- Request / Input models ------------------------------------------------
class PublishPluginInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plugin_path: Path | None = None
    zip_path: Path | None = None
    plugin_id: str | None = None
    plugin_version: str | None = None
    version_desc: str | None = None
    force: bool = False
    expect_skill_like: bool = False

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> "PublishPluginInput":
        plugin_path = Path(self.plugin_path).resolve() if self.plugin_path else None
        zip_path = Path(self.zip_path).resolve() if self.zip_path else None
        plugin_id = self._norm_optional(self.plugin_id)
        plugin_version_raw = self._norm_optional(self.plugin_version)
        plugin_version = normalize_marketplace_version_optional(plugin_version_raw)
        version_desc = self._norm_optional(self.version_desc)
        expect_skill_like = bool(self.expect_skill_like)

        if plugin_path is None and zip_path is None:
            raise ValueError("either plugin_path or zip_path must be provided")

        object.__setattr__(self, "plugin_path", plugin_path)
        object.__setattr__(self, "zip_path", zip_path)
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "plugin_version", plugin_version)
        object.__setattr__(self, "version_desc", version_desc)
        object.__setattr__(self, "expect_skill_like", expect_skill_like)
        return self

    @staticmethod
    def _norm_optional(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    zip_path: Path
    checksum_sha256: str
    plugin_id: str | None = None
    plugin_version: str | None = None
    version_desc: str | None = None
    force: bool = False

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> "PublishRequest":
        zip_path = Path(self.zip_path).resolve()
        checksum = str(self.checksum_sha256).strip().lower()
        plugin_id = self._norm_optional(self.plugin_id)
        plugin_version_raw = self._norm_optional(self.plugin_version)
        plugin_version = normalize_marketplace_version_optional(plugin_version_raw)
        version_desc = self._norm_optional(self.version_desc)

        if not zip_path.is_file():
            raise ValueError(f"zip file not found: {zip_path}")
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ValueError("checksum_sha256 must be 64-char hex")

        object.__setattr__(self, "zip_path", zip_path)
        object.__setattr__(self, "checksum_sha256", checksum)
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "plugin_version", plugin_version)
        object.__setattr__(self, "version_desc", version_desc)
        return self

    @staticmethod
    def _norm_optional(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PluginListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    search_keyword: str = ""
    plugin_type: str | None = None
    publisher_name: str | None = None
    asset_id: str | None = None
    asset_type: str | None = None
    publisher_id: str | None = None
    page: int = 1
    page_size: int = 20
    order_by: str = "install_count"
    desc: bool = True


# ---- Response / Data models ------------------------------------------------
class PluginPublishResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plugin_id: str = ""
    name: str = ""
    version: str = ""
    status: str = ""
    published_at: str = ""
    storage_url: str = ""


class SkillImportItemResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry: str = ""
    status: Literal["ok", "error", "skipped"]
    plugin_id: str | None = None
    name: str | None = None
    version: str | None = None
    error: str | None = None
    message: str | None = None


class SkillImportSummary(BaseModel):
    """``total`` counts top-level skill dirs in the bundle; with ``fail_fast``, ok+failed may be less than total."""

    model_config = ConfigDict(extra="ignore")

    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0


class SkillImportResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: SkillImportSummary
    results: list[SkillImportItemResult] = Field(default_factory=list)


class PluginVersionDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    asset_id: str = ""
    version: str = ""
    asset_type: str = ""
    plugin_type: str | None = None
    name: str = ""
    display_name: str = ""
    short_desc: str | None = None
    detail_desc: str | None = None
    publisher_id: str = ""
    publisher_name: str = ""
    tags: list[str] | None = None
    certification: str | None = None
    changelog: str | None = None
    file_path: str | None = None
    icon_uri: str | None = None


class PluginListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    asset_id: str = ""
    asset_type: str = ""
    name: str = ""
    display_name: str = ""
    plugin_type: str | None = None
    latest_version: str | None = None
    public_latest_version: str | None = None
    all_versions: list[str] = Field(default_factory=list)
    git_version_display_as_commit: bool = False
    resolved_commit_sha: str | None = None

    def display_version_for_market_search(self) -> str:
        """与前端 ``usePluginMarketConfigs.mapPlugin`` 一致：skill-like 优先展示已通过审的对外版本（库表原始串，供 install/info API）。"""
        pt = (self.plugin_type or "").strip().lower()
        if pt in ("skill", "teamskills"):
            plv = (self.public_latest_version or "").strip()
            lv = (self.latest_version or "").strip()
            return plv or lv or ""
        return (self.latest_version or "").strip() or ""

    def _market_list_latest_version_for_label(self) -> str:
        """与前端 ``mapPlugin.latestVersion`` 一致，用于版本文案格式化时的 latest 比较基准。"""
        pt = (self.plugin_type or "").strip().lower()
        if pt in ("skill", "teamskills"):
            plv = (self.public_latest_version or "").strip()
            lv = (self.latest_version or "").strip()
            return plv or lv
        return (self.latest_version or "").strip()

    def format_version_label(self, raw: str | None) -> str:
        return format_market_skill_version_label(
            raw,
            latest_version=self._market_list_latest_version_for_label(),
            git_version_display_as_commit=self.git_version_display_as_commit,
            resolved_commit_sha=self.resolved_commit_sha,
        )

    def _search_raw_versions(self) -> list[str]:
        """去重后的可见原始版本列表；与列表 API ``all_versions`` 对齐。"""
        raw_versions: list[str] = []
        seen: set[str] = set()
        for v in self.all_versions:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                raw_versions.append(s)
        if not raw_versions:
            dv = self.display_version_for_market_search()
            if dv:
                raw_versions = [dv]
        return raw_versions

    def search_versions(self) -> tuple[int, str]:
        """返回 (可见版本数, 逗号分隔版本列表)，与 install/info API 一致。"""
        raw_versions = self._search_raw_versions()
        joined = ", ".join(raw_versions) if raw_versions else "-"
        return len(raw_versions), joined


class PluginListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page: int = 1
    page_size: int = 20
    total: int = 0
    items: list[PluginListItem] = Field(default_factory=list)


class PluginVersionDeleteData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    asset_id: str = ""
    version: str = ""


class PluginDownloadData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    download_url: str = ""
    asset_id: str = ""
    name: str = ""
    version: str = ""
    file_size: int = 0
    checksum_sha256: str = ""


class DownloadArtifactResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    download_url: str
    expected_checksum_sha256: str
    actual_checksum_sha256: str
    verified: bool
