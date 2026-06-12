# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ClawHub-compatible zip text fingerprint (aligned with clawhub `hashSkillZip`)."""

from __future__ import annotations

import hashlib
import io
import zipfile

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import MAX_FILE_SIZE
from plugins_market.validation.zip_utils import DecompressCounter, safe_read_zip_member, validate_zip_safety

# Mirrors `packages/clawhub/src/schema/textFiles.ts` `TEXT_FILE_EXTENSION_SET`.
TEXT_FILE_EXTENSION_SET: frozenset[str] = frozenset(
    {
        "md",
        "mdx",
        "txt",
        "json",
        "json5",
        "yaml",
        "yml",
        "toml",
        "js",
        "cjs",
        "mjs",
        "ts",
        "tsx",
        "jsx",
        "py",
        "sh",
        "rb",
        "go",
        "rs",
        "swift",
        "kt",
        "java",
        "cs",
        "cpp",
        "c",
        "h",
        "hpp",
        "sql",
        "csv",
        "ini",
        "cfg",
        "env",
        "xml",
        "html",
        "css",
        "scss",
        "sass",
        "svg",
    }
)


def sanitize_zip_path(raw: str) -> str | None:
    """Match clawhub `sanitizeZipPath` / `sanitizeRelPath` behavior."""
    normalized = raw.replace("\\", "/").lstrip("./").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return None
    if ".." in normalized:
        return None
    return normalized


def hash_skill_zip(zip_bytes: bytes) -> tuple[list[dict[str, object]], str]:
    """
    Returns (file_rows, fingerprint_hex) where file_rows have path, sha256, size.
    fingerprint_hex matches clawhub `buildSkillFingerprint(hashSkillZip(...).files)`.
    """
    if len(zip_bytes) > MAX_FILE_SIZE:
        raise PublishError(
            code=400,
            error="zip_too_large",
            message=f"artifact exceeds MAX_FILE_SIZE ({MAX_FILE_SIZE} bytes)",
        )

    hashed: list[dict[str, object]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        validate_zip_safety(zf)
        counter = DecompressCounter()
        for info in zf.infolist():
            if info.filename.endswith("/"):
                continue
            safe = sanitize_zip_path(info.filename)
            if not safe:
                continue
            ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
            if not ext or ext not in TEXT_FILE_EXTENSION_SET:
                continue
            b = safe_read_zip_member(zf, info.filename, counter)
            digest = hashlib.sha256(b).hexdigest()
            hashed.append({"path": safe, "sha256": digest, "size": len(b)})

    hashed.sort(key=lambda r: str(r["path"]))
    payload = "\n".join(f'{r["path"]}:{r["sha256"]}' for r in hashed)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return hashed, fingerprint
