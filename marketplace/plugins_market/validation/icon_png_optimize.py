# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize and shrink marketplace ``icon.png`` before object storage upload.

策略（与前端列表约 48px 头像、详情略大展示匹配）：
- 最长边不超过 ``ICON_PUBLISH_MAX_EDGE_PX``（默认 256），等比缩放，LANCZOS。
- 统一写出为 PNG：``optimize=True`` + ``compress_level=9``，保留透明通道（RGBA）。
- 若 Pillow 无法解码或写出异常，回退为原始字节，避免阻断发布。
- 若优化后体积大于原始（少见），保留较小的一方。
"""

from __future__ import annotations

import io
from typing import Final

from plugins_market.core.logging import get_logger
from plugins_market.validation.constants import ICON_MAX_BYTES, ICON_PUBLISH_MAX_EDGE_PX, PNG_MAGIC

logger = get_logger(__name__)

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - 运行环境应已安装 pillow
    Image = None  # type: ignore[misc, assignment]
    ImageOps = None  # type: ignore[misc, assignment]

_RGBA_MODES: Final[frozenset[str]] = frozenset({"RGBA", "RGB"})


def _to_rgba_work_image(im: "Image.Image") -> "Image.Image":
    """得到带 alpha 的工作图，便于缩略与 PNG 写出。"""
    mode = im.mode
    if mode in _RGBA_MODES:
        return im
    if mode == "P":
        if "transparency" in im.info:
            return im.convert("RGBA")
        p = im.convert("RGBA")
        return p
    if mode in ("L", "LA"):
        return im.convert("RGBA")
    if mode == "1":
        return im.convert("RGBA")
    return im.convert("RGBA")


def optimize_png_icon_bytes(raw: bytes, *, max_edge: int = ICON_PUBLISH_MAX_EDGE_PX) -> bytes:
    """
    将已通过魔数校验的 PNG 字节优化后返回；失败或无效时返回 *raw*。

    不改变 API 校验上限：调用方仍应先通过 ``validate_png_icon_bytes``。
    """
    if not raw or not raw.startswith(PNG_MAGIC):
        return raw
    if Image is None:
        logger.warning("pillow 未安装，跳过 icon 优化")
        return raw
    if max_edge < 32:
        max_edge = 32

    try:
        src = io.BytesIO(raw)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.load()
            work = _to_rgba_work_image(im)
            w, h = work.size
            if w <= 0 or h <= 0:
                return raw
            if max(w, h) > max_edge:
                work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            work.save(buf, format="PNG", optimize=True, compress_level=9)
            out = buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - 任意解码/写出错误均回退
        logger.info("icon PNG 优化失败，使用原图: %s", exc)
        return raw

    if not out.startswith(PNG_MAGIC) or len(out) > ICON_MAX_BYTES:
        return raw
    if len(out) >= len(raw):
        return raw
    return out
