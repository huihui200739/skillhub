"""CLI utils for openjiuwen plugin"""

from __future__ import annotations

import hashlib
from pathlib import Path

_DEFAULT_CHUNK = 1 << 20


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
