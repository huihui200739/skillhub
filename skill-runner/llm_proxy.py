# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""控制面 LLM 代理：pod 用 per-session token 调本代理，代理换上真 key 转发上游。

真 key 只在控制面进程，永不进 pod。pod 内 agent 配 api_base={proxy}/internal/llm、
api_key=<token>。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .config import settings

logger = logging.getLogger("skill_runner.llm_proxy")


class TokenRegistry:
    """token <-> (session_id, user_id) 映射，进程内（控制面单副本运行）。"""

    def __init__(self) -> None:
        self._token_to_session: dict[str, str] = {}
        self._token_to_user: dict[str, str] = {}

    def issue(self, session_id: str, user_id: str = "") -> str:
        token = "pod-" + secrets.token_urlsafe(24)
        self._token_to_session[token] = session_id
        if user_id:
            self._token_to_user[token] = user_id
        return token

    def resolve(self, token: str) -> str | None:
        return self._token_to_session.get(token)

    def resolve_user(self, token: str) -> str:
        return self._token_to_user.get(token, "")

    def revoke_for_session(self, session_id: str) -> None:
        for tok in [t for t, s in self._token_to_session.items() if s == session_id]:
            self._token_to_session.pop(tok, None)
            self._token_to_user.pop(tok, None)

    # 池复用：per-pod token 不重发，按 session 重新绑定 / 解绑
    def bind(self, token: str, session_id: str, user_id: str = "") -> None:
        self._token_to_session[token] = session_id
        if user_id:
            self._token_to_user[token] = user_id

    def unbind(self, token: str) -> None:
        self._token_to_session.pop(token, None)
        self._token_to_user.pop(token, None)


# ── 每用户每日 token 计量（默认 500k 日限，进程内，零点 UTC 自动重置）──────────
# 格式：{user_id: {"date": "2026-06-25", "tokens": 12345}}
_user_daily_tokens: dict[str, dict] = {}


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _check_user_budget(user_id: str) -> bool:
    """返回 True 表示用户仍在预算内（可继续调 LLM）。"""
    limit = settings.user_daily_token_limit
    if not user_id or limit <= 0:
        return True
    entry = _user_daily_tokens.get(user_id)
    return not (entry and entry["date"] == _utc_today() and entry["tokens"] >= limit)


def _add_user_tokens(user_id: str, tokens: int) -> None:
    if not user_id or tokens <= 0:
        return
    today = _utc_today()
    entry = _user_daily_tokens.get(user_id)
    if entry and entry["date"] == today:
        entry["tokens"] += tokens
    else:
        _user_daily_tokens[user_id] = {"date": today, "tokens": tokens}


# ── 每会话 token 累计（前端实时展示用）────────────────────────────────────────
# 控制面代理看得见每一次真实 LLM 调用，是权威计数源（与 worker 自报的 llm_usage 互斥）
_session_tokens: dict[str, int] = {}


def _add_session_tokens(session_id: str, tokens: int) -> int:
    """累加并返回该会话当前累计 token。"""
    total = _session_tokens.get(session_id, 0) + max(tokens, 0)
    _session_tokens[session_id] = total
    return total


def clear_session_tokens(session_id: str) -> None:
    """会话结束时清理，防止字典无界增长。"""
    _session_tokens.pop(session_id, None)


token_registry = TokenRegistry()

proxy_router = APIRouter(tags=["internal-llm-proxy"])

# 懒建；测试可替换本模块的 _forward_client
_forward_client: httpx.AsyncClient | None = None


def get_forward_client() -> httpx.AsyncClient:
    global _forward_client
    if _forward_client is None:
        _forward_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0, read=settings.llm_timeout_seconds, write=10.0, pool=10.0
            ),
            verify=settings.llm_verify_ssl,
        )
    return _forward_client


async def aclose() -> None:
    global _forward_client
    if _forward_client is not None:
        await _forward_client.aclose()
        _forward_client = None


def set_forward_client_for_testing(client: httpx.AsyncClient) -> None:
    """测试钩子：注入 mock 的上游 HTTP client。生产代码不应调用。"""
    global _forward_client
    _forward_client = client


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


# 逐跳（hop-by-hop）头部：转发时两侧都必须剔除，由各自 HTTP 连接自行协商。
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
})
# 请求侧额外剔除：host 由 httpx 按上游重设，authorization 由本代理换成真 key。
_REQUEST_STRIP = _HOP_BY_HOP | {"host", "authorization"}


@proxy_router.api_route("/internal/llm/{subpath:path}", methods=["POST", "GET"])
async def llm_proxy(subpath: str, request: Request) -> StreamingResponse:
    token = _bearer(request)
    session_id = token_registry.resolve(token) if token else None
    if token is None or session_id is None:
        raise HTTPException(status_code=401, detail="invalid or missing pod token")

    # 每日 token 预算检查（在 key 校验后，避免 auth bypass；在转发前，避免多余 I/O）
    user_id = token_registry.resolve_user(token)
    if user_id and not _check_user_budget(user_id):
        raise HTTPException(
            status_code=429,
            detail=f"daily token limit {settings.user_daily_token_limit} exceeded",
        )

    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="control-plane LLM key not configured")

    upstream = settings.llm_api_base.rstrip("/") + "/" + subpath
    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_STRIP
    }
    fwd_headers["authorization"] = f"Bearer {settings.llm_api_key}"

    # Detect if this is a tool_call phase (has 'tools' or 'tool_choice' in request)
    is_tool_call = False
    try:
        body_dict = json.loads(body) if body else {}
        is_tool_call = bool(body_dict.get('tools') or body_dict.get('tool_choice'))
    except (json.JSONDecodeError, AttributeError):
        pass

    client = get_forward_client()
    upstream_req = client.build_request(
        request.method,
        upstream,
        params=request.query_params,
        content=body,
        headers=fwd_headers,
    )

    # Upstream resilience: retry once on SSL/connection errors.
    # Strategy:
    #   tool_call phase (has tools/tool_choice): synthetic [DONE] on 2nd failure (prevent agent hang)
    #   answer phase (no tools): real error on 2nd failure (don't fake success for final output)
    import ssl
    resp = None
    for attempt in range(1, 3):
        try:
            resp = await client.send(upstream_req, stream=True)
            break
        except (httpx.HTTPError, ssl.SSLError) as exc:
            if attempt == 1:
                logger.warning(
                    "upstream LLM send failed (attempt 1): %s: %s; retrying once",
                    type(exc).__name__, exc
                )
            else:
                logger.warning(
                    "upstream LLM send failed (attempt 2, giving up): %s: %s; "
                    "%s",
                    type(exc).__name__, exc,
                    "returning synthetic [DONE]" if is_tool_call else "returning error stream to worker"
                )

                if is_tool_call:
                    # Tool call phase: synthetic success to prevent agent hang
                    async def _synthetic_done():
                        yield b"data: [DONE]\n\n"
                    return StreamingResponse(
                        _synthetic_done(),
                        status_code=200,
                        headers={"content-type": "text/event-stream"},
                    )
                else:
                    # Answer phase: return error stream so worker knows it failed
                    async def _error_stream():
                        yield b"data: {\"error\": \"upstream LLM connection failed\"}\n\n"
                        yield b"data: [DONE]\n\n"
                    return StreamingResponse(
                        _error_stream(),
                        status_code=200,
                        headers={"content-type": "text/event-stream"},
                    )

    if resp is None:
        raise RuntimeError("proxy send unexpectedly None after retries")

    async def _relay_and_count():
        # 逐行扫描 SSE 流，提取最后一个 usage.total_tokens（OpenAI 格式）。
        # 用行缓冲而非全量缓冲：大输出不撑内存，只保留至多一行。
        line_buf = b""
        best_total_tokens = 0
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
                line_buf += chunk
                while b"\n" in line_buf:
                    ln, line_buf = line_buf.split(b"\n", 1)
                    ln = ln.strip()
                    if ln.startswith(b"data: ") and ln != b"data: [DONE]":
                        try:
                            obj = json.loads(ln[6:])
                            tt = (obj.get("usage") or {}).get("total_tokens")
                            if tt:
                                best_total_tokens = int(tt)
                        except Exception:  # noqa: BLE001
                            pass
        except httpx.StreamConsumed:
            # 上游为即时内容响应（非流式 / 测试桩）：直接吐已就绪的字节
            yield resp.content
        except httpx.HTTPError as exc:
            # 上游流中途断开或卡住（RemoteProtocolError / ReadTimeout 等）。
            # 绝不能让异常冒泡——否则 ASGI 拍掉连接，pod 侧 agent 读到半截 SSE 流
            # 会死等 [DONE] 而死锁 hang。补一个 [DONE] 终止帧让消费方优雅收尾。
            logger.warning("upstream LLM stream broke mid-flight: %s: %s", type(exc).__name__, exc)
            if "text/event-stream" in resp.headers.get("content-type", ""):
                yield b"data: [DONE]\n\n"
        finally:
            if best_total_tokens > 0:
                if user_id:
                    _add_user_tokens(user_id, best_total_tokens)
                # 推权威 usage 事件到本会话 SSE 流，供前端实时累计展示。
                # session_total 是控制面累计值；前端按它直接 set（非自增），
                # 与 worker 自报的 usage 互斥，避免双重计数。
                if session_id:
                    cumulative = _add_session_tokens(session_id, best_total_tokens)
                    try:
                        from .models import sse_event
                        from .session_store import store
                        sess = await store.get(session_id)
                        if sess is not None and sess.events is not None:
                            sess.events.put_nowait(sse_event(
                                "usage",
                                total_tokens=best_total_tokens,
                                session_total=cumulative,
                            ))
                    except Exception:  # noqa: BLE001 - 推送失败不影响 LLM 转发
                        pass
                logger.debug(
                    "token usage: user=%s session=%s call=%d session_total=%s day=%s",
                    user_id, session_id, best_total_tokens,
                    _session_tokens.get(session_id),
                    _user_daily_tokens.get(user_id, {}).get("tokens"),
                )
            await resp.aclose()

    resp_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    return StreamingResponse(
        _relay_and_count(),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )
