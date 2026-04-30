# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从环境变量指定的本地路径读取隐私声明 Markdown。"""

from pathlib import Path

from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

# 防止误配超大文件拖垮进程
_MAX_BYTES = 512 * 1024


def read_privacy_statement_markdown() -> str:
    raw = (settings.privacy_statement_file or "").strip()
    if not raw:
        return ""
    if "\x00" in raw:
        logger.warning("privacy_statement_file: path contains NUL, ignored")
        return ""
    try:
        path = Path(raw).expanduser()
        path = path.resolve()
        if not path.is_file():
            logger.warning("privacy_statement_file: not a file or missing: %s", path)
            return ""
        data = path.read_bytes()
        if len(data) > _MAX_BYTES:
            logger.warning(
                "privacy_statement_file: file too large (%s bytes), max %s",
                len(data),
                _MAX_BYTES,
            )
            return ""
        return data.decode("utf-8", errors="replace")
    except OSError as e:
        logger.warning("privacy_statement_file: read failed: %s", e)
        return ""
