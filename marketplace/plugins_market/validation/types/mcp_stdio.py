# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""mcp-stdio type specific validation."""

from __future__ import annotations

import zipfile

from plugins_market.validation.base import raise_invalid_structure
from plugins_market.validation.zip_utils import (
    DecompressCounter,
    has_src_tree,
    safe_read_zip_member,
    validate_png_icon_bytes,
)


def validate_mcp_stdio_layout(
    zf: zipfile.ZipFile,
    prefix: str,
    counter: DecompressCounter,
) -> dict:
    """校验 mcp-stdio 包根目录：README、可选 icon.png、非空 src/。

    若存在 icon.png 则校验为合法 PNG 及大小上限。

    Returns dict with keys: icon_path, icon_bytes, readme_path.
    """
    names = set(zf.namelist())

    readme_path = prefix + "README.md"
    if readme_path not in names:
        raise_invalid_structure(
            "插件包结构不符合要求：mcp-stdio 类型缺少 README.md"
        )

    icon_path = prefix + "icon.png"

    if not has_src_tree(names, prefix):
        raise_invalid_structure(
            "插件包结构不符合要求：mcp-stdio 类型缺少 src/ 目录"
        )

    if icon_path in names:
        icon_bytes = safe_read_zip_member(zf, icon_path, counter)
        validate_png_icon_bytes(icon_bytes, path=icon_path)
    else:
        icon_bytes = b""

    return {
        "icon_path": icon_path if icon_path in names else "",
        "icon_bytes": icon_bytes,
        "readme_path": readme_path,
    }
