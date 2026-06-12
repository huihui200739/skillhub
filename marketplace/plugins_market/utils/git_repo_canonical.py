# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 仓库 URL 全局去重键：与前端 `normalizeGitRepoUrlForDedup` 语义对齐（仅用于 http(s) 公有克隆）。"""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_git_repo_global_key(repo_url: str) -> str:
    """
    返回小写、无协议、无尾斜杠、去掉 .git 后缀的 host+path，用于判断「同一仓库」是否已被任意用户接入。
    例如 https://GitHub.com/Anthropics/skills.git/ → github.com/anthropics/skills
    """
    s = (repo_url or "").strip().lower()
    if not s:
        return ""
    if s.startswith("git@"):
        at = s.find("@")
        colon = s.find(":", at + 1)
        if at >= 0 and colon > at:
            host = s[at + 1:colon]
            path = s[colon + 1:]
            s = f"https://{host}/{path}"
    if s.endswith(".git"):
        s = s[:-4]
    s = s.rstrip("/")
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return s.replace(".git", "").rstrip("/")
    host = parsed.hostname.lower()
    path = (parsed.path or "").rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    return f"{host}{path}".rstrip("/")


def parse_git_repo_owner(repo_url: str) -> str:
    """
    从克隆 URL 解析仓库 namespace/owner（规范化 key 中 host 之后的第一段 path）。
    例如 https://github.com/Anthropics/skills → anthropics；git@github.com:foo/bar.git → foo。
    """
    key = normalize_git_repo_global_key(repo_url)
    if not key:
        return ""
    slash = key.find("/")
    if slash < 0:
        return ""
    rest = key[slash + 1:]
    if not rest:
        return ""
    owner, _, _repo = rest.partition("/")
    return owner.strip()
