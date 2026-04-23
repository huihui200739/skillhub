from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List


_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_BLOCK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_CAMEL_CASE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class BM25Document:
    choice_id: str
    payload: str
    text: str
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BM25FinderConfig:
    top_k: int = 10
    k1: float = 1.5
    b: float = 0.75
    delta: float = 0.5


def split_identifier_terms(value: str) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    expanded = _CAMEL_CASE_RE.sub(" ", text)
    expanded = expanded.replace(".", " ").replace("_", " ").replace("-", " ").replace("/", " ")
    return [token.lower() for token in _ASCII_TOKEN_RE.findall(expanded)]


def lexical_tokenize(text: str) -> List[str]:
    raw = str(text or "").strip().lower()
    if not raw:
        return []
    tokens: List[str] = []
    tokens.extend(_ASCII_TOKEN_RE.findall(raw))
    for match in _CJK_BLOCK_RE.finditer(raw):
        block = match.group(0)
        if not block:
            continue
        tokens.append(block)
        if len(block) == 1:
            continue
        tokens.extend(block[index: index + 2] for index in range(len(block) - 1))
    return tokens


def build_bm25_document_text(*, choice_id: str, payload: str, description: str = "", text: str = "") -> str:
    parts: List[str] = []
    clean_choice_id = str(choice_id or "").strip()
    clean_payload = str(payload or "").strip()
    clean_description = str(description or "").strip()
    clean_text = str(text or "").strip()
    if clean_choice_id:
        parts.append(clean_choice_id)
        parts.extend(split_identifier_terms(clean_choice_id))
    if clean_payload:
        parts.append(clean_payload)
        parts.extend(split_identifier_terms(clean_payload))
    if clean_description:
        parts.append(clean_description)
    if clean_text:
        parts.append(clean_text)
    return "\n".join(part for part in parts if str(part).strip())


def build_embedding_record_text(
    *,
    name: str,
    description: str = "",
    retrieval_text: str = "",
    skill_id: str = "",
    cid: str = "",
) -> str:
    parts: List[str] = []
    clean_name = str(name or "").strip()
    clean_description = str(description or "").strip()
    clean_retrieval_text = str(retrieval_text or "").strip()
    clean_skill_id = str(skill_id or "").strip()
    clean_cid = str(cid or "").strip()
    if clean_name:
        parts.append(clean_name)
    if clean_description:
        parts.append(clean_description)
    if clean_retrieval_text:
        parts.append(clean_retrieval_text)
    if clean_skill_id:
        parts.append(clean_skill_id)
        parts.extend(split_identifier_terms(clean_skill_id))
    if clean_cid:
        parts.append(clean_cid)
        parts.extend(split_identifier_terms(clean_cid))
    return "\n".join(parts)


__all__ = [
    "BM25Document",
    "BM25FinderConfig",
    "build_bm25_document_text",
    "build_embedding_record_text",
    "lexical_tokenize",
    "split_identifier_terms",
]
