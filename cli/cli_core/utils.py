# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CLI utils for openjiuwen plugin"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_DEFAULT_CHUNK = 1 << 20

# Align with marketplace MAX_FILE_SIZE (512 MB).
CLI_ARTIFACT_DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024

# Terminal output: strip C0/C1 control chars except tab/newline/carriage-return.
_TERMINAL_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_terminal_text(value: object) -> str:
    """Neutralize terminal escape/control sequences in user-controlled CLI output."""
    text = "" if value is None else str(value)
    return _TERMINAL_CONTROL_RE.sub("", text)


def sha256_file_hex(path: Path, *, chunk_size: int = _DEFAULT_CHUNK) -> str:
    """Stream a local file and return SHA-256 as a lowercase hex string."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
