# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 克隆 URL 安全校验：拒绝内网/本机/链路本地等 SSRF 目标。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from plugins_market.core.errors import PublishError

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
    }
)


def _is_unsafe_ip_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    )


def _hostname_resolves_to_blocked_ip(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES:
        return True
    if host.endswith(".localhost") or host.endswith(".local"):
        return True

    try:
        if host.startswith("[") and host.endswith("]"):
            addr = ipaddress.ip_address(host[1:-1])
            return _is_unsafe_ip_address(addr)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # 无法解析时不放行，避免「校验通过、fetch 时再解析到内网」的边缘路径
        return True

    if not infos:
        return True

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_unsafe_ip_address(addr):
            return True
    return False


def assert_resolved_git_host_allowed(hostname: str) -> None:
    """对主机名做 DNS 解析并拒绝内网/本机等目标；注册与 git fetch 前均应调用。"""
    host = (hostname or "").strip()
    if not host:
        raise PublishError(
            code=400,
            error="invalid_repo_url",
            message="无效的仓库 URL",
            error_code="SKILLHUB_GIT_SYNC_REPO_URL_INVALID",
            error_class="validation",
        )
    if _hostname_resolves_to_blocked_ip(host):
        raise PublishError(
            code=400,
            error="invalid_repo_url",
            message="不允许访问内网、本机、保留地址或无法安全解析的 Git 仓库主机",
            error_code="SKILLHUB_GIT_SYNC_REPO_URL_INVALID",
            error_class="validation",
        )


def assert_public_git_clone_url(repo_url: str) -> str:
    """校验 http(s) 克隆地址且解析后不落内网；通过则返回去首尾空白的原串。"""
    raw = (repo_url or "").strip()
    if not raw:
        raise PublishError(
            code=400,
            error="invalid_repo_url",
            message="repo_url 不能为空",
            error_code="SKILLHUB_GIT_SYNC_REPO_URL_INVALID",
            error_class="validation",
        )
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise PublishError(
            code=400,
            error="invalid_repo_url",
            message="仅支持 http(s) 克隆地址",
            error_code="SKILLHUB_GIT_SYNC_REPO_URL_INVALID",
            error_class="validation",
        )
    host = (parsed.hostname or "").strip()
    if not host:
        raise PublishError(
            code=400,
            error="invalid_repo_url",
            message="无效的仓库 URL",
            error_code="SKILLHUB_GIT_SYNC_REPO_URL_INVALID",
            error_class="validation",
        )
    if parsed.username or parsed.password:
        raise PublishError(
            code=400,
            error="invalid_repo_url",
            message="克隆地址请勿包含用户名或密码",
            error_code="SKILLHUB_GIT_SYNC_REPO_URL_INVALID",
            error_class="validation",
        )
    assert_resolved_git_host_allowed(host)
    return raw
