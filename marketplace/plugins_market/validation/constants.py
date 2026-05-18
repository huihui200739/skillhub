# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared constants for plugin validation."""

import re

# ---------------------------------------------------------------------------
# Name patterns
# ---------------------------------------------------------------------------

# Generic plugin name: ^[a-z][a-z0-9-]*$
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Skill name: starts with lowercase letter, segments separated by single hyphens,
# no leading/trailing hyphen, no consecutive '--'.
# Equivalent to CLI: NAME_PATTERN + SKILL_NAME_PATTERN + _validate_skill_slug
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX_LEN = 64

# Tool name inside schemas/tools.json (same as generic name)
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# Git 同步无 SKILL 声明版本时：使用 commit SHA 前缀（7–32 位小写 hex，与库表 version 列 varchar(32) 对齐）
GIT_COMMIT_VERSION_PATTERN = re.compile(r"^[0-9a-f]{7,32}$")


def is_valid_market_version(version: str) -> bool:
    """semver x.y.z 或 Git commit 版本（hex）。"""
    v = (version or "").strip()
    if not v:
        return False
    if VERSION_PATTERN.match(v):
        return True
    return bool(GIT_COMMIT_VERSION_PATTERN.match(v.lower()))

# ---------------------------------------------------------------------------
# Runtime types
# ---------------------------------------------------------------------------

RUNTIME_SKILL = "skill"
RUNTIME_TOOLS = "tools"
RUNTIME_MCP_STDIO = "mcp-stdio"
RUNTIME_RESTFUL_API = "restful-api"
SUPPORTED_RUNTIME_TYPES = {RUNTIME_SKILL, RUNTIME_TOOLS, RUNTIME_MCP_STDIO, RUNTIME_RESTFUL_API}

# ---------------------------------------------------------------------------
# File / zip size limits
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB – raw zip upload limit
MAX_ZIP_ENTRIES = 1000  # max number of entries in a zip
MAX_DECOMPRESSED_TOTAL = 512 * 1024 * 1024  # 512 MB – cumulative decompressed bytes
MAX_COMPRESSION_RATIO = 50  # pre-check only; real guard is byte counter

ZIP_STREAM_READ_CHUNK_BYTES = 64 * 1024  # streaming zip reads, uploads, bundle I/O (64 KiB)

# Zip entry path: Windows drive-letter prefix (validate_zip_safety + skill bundle extract)
ZIP_ENTRY_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")

# ---------------------------------------------------------------------------
# YAML parsing limits
# ---------------------------------------------------------------------------

MAX_YAML_BYTES = 1 * 1024 * 1024  # 1 MB per YAML document
YAML_MAX_DEPTH = 100
YAML_MAX_ALIASES = 1000
YAML_MAX_SCALAR_LEN = 1 * 1024 * 1024  # 1 MB per scalar string

# ---------------------------------------------------------------------------
# JSON parsing limits
# ---------------------------------------------------------------------------

MAX_JSON_BYTES = 10 * 1024 * 1024  # 10 MB：tools.json 校验与 skill-import 的 manifest.json 读取上限

# ---------------------------------------------------------------------------
# Field length limits
# ---------------------------------------------------------------------------

DISPLAY_NAME_MAX_LEN = 128
PLUGIN_YAML_DESCRIPTION_MAX_LEN = 4096
SKILL_DESC_MAX_LEN = 4096
# 与 models.market_assets.MarketAssetDB.short_desc 列宽一致；较长文案走 detail_desc（Text）
MARKET_ASSET_SHORT_DESC_MAX_LEN = 4096

# ---------------------------------------------------------------------------
# Icon / PNG（仅当包内存在 icon.png 时校验；无则跳过校验且不写入占位对象）
# ---------------------------------------------------------------------------

PNG_MAGIC = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"  # 8-byte PNG signature
ICON_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
# 写入 OBS 前：icon 最长边像素上限（列表约 48px，256 已覆盖常见高 DPR）
ICON_PUBLISH_MAX_EDGE_PX = 256
