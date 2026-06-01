# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


MiB = 1024 * 1024

MAX_TEXT_FILE_BYTES = 2 * MiB
MAX_MANIFEST_BYTES = 2 * MiB
MAX_TREE_INDEX_BYTES = 16 * MiB
MAX_CATALOG_BYTES = 64 * MiB
MAX_JSON_ARTIFACT_BYTES = 128 * MiB
MAX_STORAGE_OBJECT_BYTES = 128 * MiB
MAX_ZIP_ARCHIVE_BYTES = 512 * MiB
MAX_ZIP_MEMBER_BYTES = 128 * MiB
MAX_ZIP_UNCOMPRESSED_BYTES = 1024 * MiB
MAX_ZIP_MEMBERS = 20_000

_READ_CHUNK_SIZE = 1024 * 1024


def resolve_size_limit(env_name: str, default: int) -> int:
    raw = str(os.getenv(env_name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError:
        return int(default)
    return value if value > 0 else int(default)


def ensure_path_size(path: str | Path, *, max_bytes: int, label: str = "file") -> Path:
    target = Path(path)
    size = target.stat().st_size
    if size > int(max_bytes):
        raise ValueError(f"{label} is too large: {target} ({size} bytes > {int(max_bytes)} bytes)")
    return target


def read_text_file(
    path: str | Path,
    *,
    max_bytes: int = MAX_TEXT_FILE_BYTES,
    encoding: str = "utf-8",
    label: str = "text file",
) -> str:
    target = ensure_path_size(path, max_bytes=max_bytes, label=label)
    return target.read_text(encoding=encoding)


def read_limited_stream(stream: BinaryIO, *, max_bytes: int, label: str = "stream") -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > int(max_bytes):
            raise ValueError(f"{label} is too large: {total} bytes > {int(max_bytes)} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
