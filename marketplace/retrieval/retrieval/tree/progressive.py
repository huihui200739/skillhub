from __future__ import annotations

import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Dict, List, Protocol, Sequence

from models.retrieval import (
    FinderCandidate,
    FinderItem,
    FinderNode,
    FinderTrace,
    FinderTraceEvent,
    RetrieverChoice,
)
from retrieval.tree.disclosure import (
    DisclosureConfig,
    ExposedFragment,
    SelectableResolution,
    build_disclosure_messages,
    build_exposed_fragment,
    parse_selected_codes,
)

_FROM_PREFIX_RE = re.compile(r"^\s*From\s+[^:]+:\s*", re.IGNORECASE)
_ABSTAIN_HINT_RE = re.compile(
    r"(none|no suitable|no relevant|not relevant|unrelated|cannot determine|can't determine|"
    r"无合适|没有合适|无相关|没有相关|不相关|无匹配|没有匹配|无法判断|无法确定|均与|都与)",
    re.IGNORECASE,
)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_BOUNDARY_CODE_ALPHABET = "123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CompletionClient(Protocol):
    def complete(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int | None = None,
        stop_sequences: List[str] | None = None,
        extra_body_override: Dict[str, Any] | None = None,
        n: int = 1,
        request_timeout: float | None = None,
    ) -> List[str]:
        ...


@dataclass(frozen=True)
class _FinderNodeStats:
    subtree_item_count: int = 0
    subtree_depth: int = 0


@dataclass(frozen=True)
class ProgressiveFinderConfig:
    top_k: int = 5
    batch_size: int = 1
    max_tokens: int = 48
    trie_constrained_decoding_enabled: bool = False
    trie_constraint_allow_user_nodes: bool = True
    trie_constraint_max_candidates: int = 512
    trie_constraint_fallback_payload: str = ""
    max_branch_choices: int = 6
    auto_expand_child_threshold: int = 3
    collapse_single_chain: bool = True
    max_collapse_steps: int = 8
    max_parallel_branches: int = 3
    enable_parallel_branches: bool = True
    auto_terminal_item_threshold: int = 12
    branch_choice_slack: int = 2
    branch_candidate_slack: int = 1
    round_robin_branch_reduce: bool = True
    branch_max_tokens: int = 96
    item_max_tokens: int = 128
    request_timeout: float | None = None
    compact_boundary_codes_enabled: bool = False
    max_exposure_depth_per_call: int = 2
    exposure_threshold: int = 12
    force_expand_single_child: bool = True


@dataclass
class ProgressiveFinderResult:
    candidates: List[FinderCandidate]
    trace: FinderTrace
    candidate_records: List[Dict[str, object]] = field(default_factory=list)
    summary_lines: List[str] = field(default_factory=list)
    selected_payload: str | None = None
    selected_rank: int = -1
    raw_outputs: List[str] = field(default_factory=list)
    request_messages: List[Dict[str, str]] = field(default_factory=list)
    elapsed_ms: float = 0.0


def _format_sender_message(sender: str, message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    if _FROM_PREFIX_RE.match(text):
        return text
    sender_text = str(sender or "User").strip() or "User"
    return f"From {sender_text}: {text}"


def _normalize_query_messages(query: str | Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    if isinstance(query, str):
        text = _format_sender_message("User", query)
        return [{"role": "user", "content": text or "From User: (empty)"}]

    lines: List[str] = []
    for message in query:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        content = str(message.get("content") or "").strip()
        if not content or role == "system":
            continue
        if role == "assistant":
            sender = "Assistant"
        elif role == "user":
            sender = "User"
        else:
            sender = role[:1].upper() + role[1:] if role else "Runtime"
        lines.append(_format_sender_message(sender, content))
    if not lines:
        return [{"role": "user", "content": "From User: (empty)"}]
    return [{"role": "user", "content": "\n".join(lines)}]


def _merged_extra_body(extra_body_override: Dict[str, Any] | None = None) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.0,
        "top_p": 1.0,
    }
    if not extra_body_override:
        return merged
    for key, value in extra_body_override.items():
        if key == "thinking" and isinstance(value, dict) and isinstance(merged.get("thinking"), dict):
            nested = dict(merged["thinking"])
            nested.update(value)
            merged["thinking"] = nested
            continue
        if key == "chat_template_kwargs" and isinstance(
                value, dict) and isinstance(merged.get("chat_template_kwargs"), dict):
            nested = dict(merged["chat_template_kwargs"])
            nested.update(value)
            merged["chat_template_kwargs"] = nested
            continue
        merged[key] = value
    return merged


def _resolution_payload_map(resolutions: Dict[str, SelectableResolution]) -> Dict[str, str]:
    payloads: Dict[str, str] = {}
    for code, resolution in resolutions.items():
        payloads[code] = resolution.canonical_id
    return payloads


def _selected_terminal_ids(selected: Sequence[SelectableResolution]) -> List[str]:
    terminal_ids: List[str] = []
    for item in selected:
        if item.is_terminal and item.item is not None:
            terminal_ids.append(item.item.item_id)
    return terminal_ids


def _selected_branch_ids(selected: Sequence[SelectableResolution]) -> List[str]:
    branch_ids: List[str] = []
    for item in selected:
        if not item.is_terminal and item.node is not None:
            branch_ids.append(item.node.node_id)
    return branch_ids


def _build_node_selection_prompt(*, node: FinderNode, top_k: int) -> str:
    lines = [
        "/no_think",
        "你是一个 finder，只负责从当前层的 category 中继续下钻选择。",
        "你只根据当前节点的直接子节点做判断，不做规划，不输出解释。",
        f"当前节点: {node.label} ({node.node_id})",
    ]
    if node.description:
        lines.append(f"当前节点描述: {node.description}")
    lines.extend(
        [
            "",
            "规则:",
            f"- 从下列子节点中选出最相关的 {top_k} 个 category。",
            "- 优先下钻到与用户任务对象直接匹配的分支，不要优先选择过于泛化的研究、写作、创意或规划分支。",
            "- 如果某个分支明显对应特定对象或产物类型，例如论文/文献、天气/预报、地图/地点、新闻/资讯、图像/视频、代码/文档，就优先保留该分支。",
            "- 每行只输出 1 个 node id。",
            "- 只能输出列表中的 node id。",
            "- 不要输出编号、解释、JSON、Markdown 或额外文本。",
            "",
            "可选子节点:",
        ]
    )
    for child in node.children:
        detail = f": {child.description}" if child.description else ""
        lines.append(f"- {child.node_id} | {child.label}{detail}")
    return "\n".join(lines)


def _build_item_selection_prompt(*, node: FinderNode, items: Sequence[FinderItem], top_k: int) -> str:
    lines = [
        "/no_think",
        "你是一个 finder，只负责从当前候选中选出最适合执行用户请求的 item。",
        f"当前节点: {node.label} ({node.node_id})",
    ]
    if node.description:
        lines.append(f"当前节点描述: {node.description}")
    lines.extend(
        [
            "",
            "规则:",
            f"- 从下列 item 中选出最相关的 {top_k} 个。",
            "- 优先选择能直接处理当前对象与产物类型的专用 item，不要让泛化 research / writing / planning / summarize 工具盖过更直接的专用工具。",
            "- 如果用户是在找论文、文献、最新研究、推荐论文，优先选择论文/文献检索类 item；如果用户是在解读、总结、分析某一篇具体论文或文章，优先选择论文/文章阅读解析类 item。",
            "- 如果用户需要实时事实型信息，例如天气、预报、预警、地点、路线、新闻源抓取，优先选择对应的事实查询类 item，而不是泛化调研或内容生成类 item。",
            "- 每行只输出 1 个 item id。",
            "- 只能输出列表中的 item id。",
            "- 不要输出编号、解释、JSON、Markdown 或额外文本。",
            "",
            "候选 item:",
        ]
    )
    for item in items:
        label = item.label or item.item_id
        detail = f": {item.description}" if item.description else ""
        lines.append(f"- {item.item_id} | {label}{detail}")
    return "\n".join(lines)


@dataclass(frozen=True)
class _VisibleOption:
    display_name: str
    canonical_id: str
    label: str
    description: str
    kind: str
    prompt_text: str = ""


def _path_segments(value: str) -> List[str]:
    text = str(value or "").strip().replace("/", ".")
    return [part for part in text.split(".") if part]


def _to_pascal_case(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _CAMEL_BOUNDARY_RE.sub("-", text)
    text = text.replace("_", "-")
    text = _NON_ALNUM_RE.sub("-", text)
    text = re.sub(r"-{2,}", "-", text)
    parts = [part.lower() for part in text.strip("-").split("-") if part]
    if not parts:
        return ""
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _encode_boundary_code(index: int, *, width: int) -> str:
    if width <= 0:
        raise ValueError("width must be positive")
    base = len(_BOUNDARY_CODE_ALPHABET)
    value = max(0, int(index))
    encoded = []
    for _ in range(width):
        encoded.append(_BOUNDARY_CODE_ALPHABET[value % base])
        value //= base
    return "".join(reversed(encoded))


def _build_compact_boundary_codes(count: int) -> List[str]:
    if count <= 0:
        return []
    base = len(_BOUNDARY_CODE_ALPHABET)
    width = 1
    capacity = base
    while count > capacity:
        width += 1
        capacity *= base
    return [_encode_boundary_code(index, width=width) for index in range(count)]


def _build_visible_options(
    *,
    branches: Sequence[FinderNode] = (),
    items: Sequence[FinderItem] = (),
    compact_codes_enabled: bool = False,
) -> List[_VisibleOption]:
    raw_entries: List[Dict[str, str]] = []
    for child in branches:
        raw_entries.append(
            {
                "canonical_id": child.node_id,
                "label": child.label or child.node_id,
                "description": child.description or "",
                "kind": "branch",
            }
        )
    for item in items:
        canonical_id = item.payload or item.item_id
        raw_entries.append(
            {
                "canonical_id": canonical_id,
                "label": item.label or canonical_id,
                "description": item.description or "",
                "kind": "item",
            }
        )

    if not raw_entries:
        return []

    if compact_codes_enabled:
        display_names = _build_compact_boundary_codes(len(raw_entries))
    else:
        segment_lists = [_path_segments(entry["canonical_id"]) for entry in raw_entries]
        display_names = [_to_pascal_case(segments[-1] if segments else entry["canonical_id"]) or (
            segments[-1] if segments else entry["canonical_id"]) for entry, segments in zip(raw_entries, segment_lists)]

        while len(set(display_names)) < len(display_names):
            grouped: Dict[str, List[int]] = {}
            for index, name in enumerate(display_names):
                grouped.setdefault(name, []).append(index)
            for indices in grouped.values():
                if len(indices) <= 1:
                    continue
                for index in indices:
                    segments = segment_lists[index]
                    if len(segments) > 1:
                        current_depth = len(display_names[index].split("/"))
                        next_depth = min(len(segments), current_depth + 1)
                        display_names[index] = "/".join((_to_pascal_case(segment) or segment)
                                                        for segment in segments[-next_depth:])
                    else:
                        display_names[index] = f"{display_names[index]}__{index + 1}"

    options: List[_VisibleOption] = []
    for entry, display_name in zip(raw_entries, display_names):
        prompt_text = f"- {display_name} | {entry['kind']} | {entry['label']}"
        if entry["description"]:
            prompt_text = f"{prompt_text} | {entry['description']}"
        options.append(
            _VisibleOption(
                display_name=display_name,
                canonical_id=entry["canonical_id"],
                label=entry["label"],
                description=entry["description"],
                kind=entry["kind"],
                prompt_text=prompt_text,
            )
        )
    return options


def _build_visible_subtree_prompt(
    *,
    node: FinderNode,
    options: Sequence[_VisibleOption],
    top_k: int,
    compact_codes_enabled: bool,
) -> str:
    lines = [
        "/no_think",
        "You are a retrieval router.",
        "You can only see the current visible subtree shown below.",
        "Select the most relevant visible boundary nodes for the user request.",
        f"Return at most {top_k} {'codes' if compact_codes_enabled else 'names'}.",
        f"Output one {'code' if compact_codes_enabled else 'display name'} per line.",
        f"Only output the {'codes' if compact_codes_enabled else 'display names'} exactly as shown.",
        "If none are relevant, output 0.",
        "Do not output explanations, JSON, Markdown, numbering, or full paths.",
        "",
        f"Current visible subtree root: {node.label} ({node.node_id})",
    ]
    if node.description:
        lines.append(f"Root description: {node.description}")
    lines.extend(["", "Visible boundary nodes:"])
    for option in options:
        lines.append(option.prompt_text)
    return "\n".join(lines)


def _is_abstain_output(output: str) -> bool:
    text = str(output or "").strip()
    if not text:
        return False
    if text == "0":
        return True
    return _ABSTAIN_HINT_RE.search(text) is not None


class ProgressiveFinder:
    def __init__(
        self,
        *,
        llm: CompletionClient,
        config: ProgressiveFinderConfig | None = None,
        debug_event_hook: Any | None = None,
    ) -> None:
        self._llm = llm
        self._config = config or ProgressiveFinderConfig()
        self._debug_event_hook = debug_event_hook
        self._node_stats_cache: Dict[int, _FinderNodeStats] = {}
        self._node_stats_lock = threading.Lock()

    def retrieve_top_k(
        self,
        *,
        model: str,
        query: str | Sequence[Dict[str, str]],
        choices: Sequence[RetrieverChoice],
        resolve_candidate: Callable[[str, Dict[str, str]], str],
        system_prompt: str,
        top_k: int | None = None,
        prefix_audit_hook: Callable[[str, str, List[Dict[str, str]]], None] | None = None,
        before_llm_call_hook: Callable[[], None] | None = None,
    ) -> ProgressiveFinderResult:
        started = perf_counter()
        with self._node_stats_lock:
            self._node_stats_cache = {}
        resolved_top_k = max(1, int(top_k if top_k is not None else self._config.top_k))
        query_messages = _normalize_query_messages(query)
        messages = [{"role": "system", "content": str(system_prompt or "")}] + query_messages
        if prefix_audit_hook is not None:
            prefix_audit_hook("retriever", model, messages)
        self._record_debug_event(
            {
                "type": "progressive_action",
                "phase": "disclose_candidates",
                "model": model,
                "choice_count": len(choices),
                "top_k": resolved_top_k,
                "batch_size": int(self._config.batch_size),
                "trie_constrained_decoding_enabled": bool(self._config.trie_constrained_decoding_enabled),
            }
        )
        root = FinderNode(
            node_id="flat_root",
            label="Flat Root",
            items=tuple(
                FinderItem(
                    item_id=str(choice.choice_id),
                    payload=str(choice.payload),
                    label=str(choice.choice_id),
                    description=str(choice.description or ""),
                )
                for choice in choices
            ),
        )
        trace = FinderTrace()
        result = self._select_items(
            model=model,
            query_messages=query_messages,
            node=root,
            depth=0,
            top_k=resolved_top_k,
            trace=trace,
            branch_path=(root.node_id,),
            allowed_payloads={str(choice.choice_id): str(choice.payload) for choice in choices},
            resolve_candidate=resolve_candidate,
            system_prompt_override=str(system_prompt or ""),
            before_llm_call_hook=before_llm_call_hook,
        )
        trace.record(
            "search_complete",
            node_id=root.node_id,
            depth=0,
            detail={
                "candidate_count": len(
                    result.candidates),
                "top_k": resolved_top_k})
        self._record_debug_event(
            {
                "type": "progressive_action",
                "phase": "selection_complete",
                "model": model,
                "candidate_count": len(result.candidate_records),
                "valid_candidate_count": len(result.candidates),
                "selected_payload": result.selected_payload,
                "selected_rank": result.selected_rank,
            }
        )
        result.trace = trace
        result.request_messages = messages
        result.elapsed_ms = round((perf_counter() - started) * 1000, 2)
        return result

    def search(
        self,
        *,
        model: str,
        query: str | Sequence[Dict[str, str]],
        root: FinderNode,
        top_k: int | None = None,
    ) -> ProgressiveFinderResult:
        started = perf_counter()
        with self._node_stats_lock:
            self._node_stats_cache = {}
        resolved_top_k = max(1, int(top_k if top_k is not None else self._config.top_k))
        query_messages = _normalize_query_messages(query)
        trace = FinderTrace()
        candidates = self._search_node(
            model=model,
            query_messages=query_messages,
            node=root,
            depth=0,
            top_k=resolved_top_k,
            trace=trace,
            branch_path=(root.node_id,),
        )
        ranked = [
            FinderCandidate(
                rank=index,
                item_id=candidate.item_id,
                payload=candidate.payload,
                branch_path=candidate.branch_path,
                label=candidate.label,
                description=candidate.description,
            )
            for index, candidate in enumerate(self._dedupe_candidates(candidates)[:resolved_top_k], start=1)
        ]
        trace.record(
            "search_complete",
            node_id=root.node_id,
            depth=0,
            detail={"candidate_count": len(ranked), "top_k": resolved_top_k},
        )
        return ProgressiveFinderResult(
            candidates=ranked,
            trace=trace,
            candidate_records=[
                {
                    "rank": candidate.rank,
                    "raw_output": candidate.item_id,
                    "resolved_payload": candidate.payload,
                    "valid": True,
                    "selected": candidate.rank == 1,
                    "choice_id": candidate.item_id,
                }
                for candidate in ranked
            ],
            summary_lines=[
                f"{candidate.rank}. {candidate.item_id} -> {candidate.payload} (ok)"
                for candidate in ranked
            ],
            selected_payload=ranked[0].payload if ranked else None,
            selected_rank=ranked[0].rank if ranked else -1,
            raw_outputs=[],
            request_messages=query_messages,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )

    def _search_node(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        depth: int,
        top_k: int,
        trace: FinderTrace,
        branch_path: tuple[str, ...],
    ) -> List[FinderCandidate]:
        fragment = self._build_fragment(node=node, branch_path=branch_path)
        trace.record(
            "fragment_built",
            node_id=node.node_id,
            depth=depth,
            detail={
                "selectable_count": len(fragment.code_to_resolution),
                "rendered_tree": fragment.rendered_tree,
                "max_exposure_depth_per_call": int(self._config.max_exposure_depth_per_call),
                "exposure_threshold": int(self._config.exposure_threshold),
            },
        )
        selectable_resolutions = list(fragment.code_to_resolution.values())
        if not selectable_resolutions:
            return []
        if len(selectable_resolutions) == 1:
            only = selectable_resolutions[0]
            trace.record(
                "fragment_selected",
                node_id=node.node_id,
                depth=depth,
                detail={
                    "mode": "single_selectable_shortcut",
                    "selected_codes": [only.code],
                    "selected_canonical_ids": [only.canonical_id],
                },
            )
            return self._continue_from_resolutions(
                model=model,
                query_messages=query_messages,
                node=node,
                selected=selectable_resolutions,
                depth=depth,
                top_k=top_k,
                trace=trace,
            )
        output, selected = self._select_from_fragment(
            model=model,
            query_messages=query_messages,
            node=node,
            depth=depth,
            top_k=top_k,
            trace=trace,
            fragment=fragment,
        )
        if not selected and _is_abstain_output(output):
            trace.record(
                "fragment_continue",
                node_id=node.node_id,
                depth=depth,
                detail={
                    "selected_codes": [],
                    "selected_terminal_ids": [],
                    "selected_branch_ids": [],
                    "mode": "abstain"},
            )
            return []
        return self._continue_from_resolutions(
            model=model,
            query_messages=query_messages,
            node=node,
            selected=selected,
            depth=depth,
            top_k=top_k,
            trace=trace,
        )

    def _build_fragment(
        self,
        *,
        node: FinderNode,
        branch_path: tuple[str, ...],
    ) -> ExposedFragment:
        return build_exposed_fragment(
            root=node,
            branch_path=branch_path,
            config=DisclosureConfig(
                max_exposure_depth_per_call=max(0, int(self._config.max_exposure_depth_per_call)),
                exposure_threshold=max(0, int(self._config.exposure_threshold)),
                force_expand_single_child=bool(self._config.force_expand_single_child),
                compact_boundary_codes_enabled=bool(self._config.compact_boundary_codes_enabled),
            ),
            subtree_item_count=lambda current: self._analyze_node(current).subtree_item_count,
        )

    def _select_from_fragment(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        depth: int,
        top_k: int,
        trace: FinderTrace,
        fragment: ExposedFragment,
    ) -> tuple[str, List[SelectableResolution]]:
        messages = build_disclosure_messages(fragment=fragment, query_messages=query_messages)
        output = self._complete(
            model=model,
            system_prompt=str(messages[0]["content"]),
            query_messages=[messages[1]],
            max_tokens=max(1, int(self._config.item_max_tokens)),
            trace=trace,
            node_id=node.node_id,
            depth=depth,
            stage="select_fragment",
            extra_body_override=self._build_constraint_extra_body(
                choice_id_to_payload=_resolution_payload_map(fragment.code_to_resolution),
                excluded_choice_ids=[],
                top_k=top_k,
            ),
            before_llm_call_hook=None,
        )
        selected = parse_selected_codes(fragment=fragment, output=output)[: max(1, int(top_k))]
        trace.record(
            "fragment_selected",
            node_id=node.node_id,
            depth=depth,
            detail={
                "selected_codes": [item.code for item in selected],
                "selected_canonical_ids": [item.canonical_id for item in selected],
                "raw_output": output,
            },
        )
        return output, selected

    def _continue_from_resolutions(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        selected: Sequence[SelectableResolution],
        depth: int,
        top_k: int,
        trace: FinderTrace,
    ) -> List[FinderCandidate]:
        trace.record(
            "fragment_continue",
            node_id=node.node_id,
            depth=depth,
            detail={
                "selected_codes": [item.code for item in selected],
                "selected_terminal_ids": _selected_terminal_ids(selected),
                "selected_branch_ids": _selected_branch_ids(selected),
            },
        )
        branch_top_k = self._resolve_branch_top_k(top_k=top_k, branch_count=max(1, len(selected)))
        grouped_results: List[List[FinderCandidate] | None] = [None] * len(selected)
        branch_indexes: List[int] = []
        for index, item in enumerate(selected):
            if not item.is_terminal and item.node is not None:
                branch_indexes.append(index)
        if branch_indexes and len(branch_indexes) > 1 and self._config.enable_parallel_branches:
            max_workers = min(len(branch_indexes), max(1, int(self._config.max_parallel_branches)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {
                    executor.submit(
                        self._search_node,
                        model=model,
                        query_messages=query_messages,
                        node=selected[index].node,
                        depth=depth + 1,
                        top_k=branch_top_k,
                        trace=trace,
                        branch_path=selected[index].branch_path,
                    ): index
                    for index in branch_indexes
                }
                for future, index in future_to_index.items():
                    grouped_results[index] = future.result()
        for index, resolution in enumerate(selected):
            if resolution.is_terminal and resolution.item is not None:
                grouped_results[index] = [
                    FinderCandidate(
                        rank=1,
                        item_id=resolution.item.item_id,
                        payload=resolution.item.payload,
                        branch_path=resolution.branch_path,
                        label=resolution.item.label,
                        description=resolution.item.description,
                    )
                ]
            elif resolution.node is not None and grouped_results[index] is None:
                grouped_results[index] = self._search_node(
                    model=model,
                    query_messages=query_messages,
                    node=resolution.node,
                    depth=depth + 1,
                    top_k=branch_top_k,
                    trace=trace,
                    branch_path=resolution.branch_path,
                )
        reduced = self._merge_branch_candidates(branch_results=grouped_results, top_k=top_k)
        trace.record(
            "reduce_complete",
            node_id=node.node_id,
            depth=depth,
            detail={
                "input_candidates": sum(len(group or []) for group in grouped_results),
                "output_candidates": len(reduced),
                "mode": "round_robin" if self._config.round_robin_branch_reduce else "sequential",
            },
        )
        return reduced

    def _search_children(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        selected_children: List[FinderNode],
        depth: int,
        top_k: int,
        trace: FinderTrace,
        branch_path: tuple[str, ...],
    ) -> List[FinderCandidate]:
        child_ids = [child.node_id for child in selected_children]
        trace.record(
            "branch_fork",
            node_id=node.node_id,
            depth=depth,
            detail={
                "selected_child_ids": child_ids,
                "parallel": len(selected_children) > 1 and self._config.enable_parallel_branches,
                "branch_top_k": self._resolve_branch_top_k(top_k=top_k, branch_count=len(selected_children)),
            },
        )
        branch_top_k = self._resolve_branch_top_k(top_k=top_k, branch_count=len(selected_children))
        if len(selected_children) <= 1 or not self._config.enable_parallel_branches:
            branch_results = [
                self._search_node(
                    model=model,
                    query_messages=query_messages,
                    node=child,
                    depth=depth + 1,
                    top_k=branch_top_k,
                    trace=trace,
                    branch_path=branch_path + (child.node_id,),
                )
                for child in selected_children
            ]
        else:
            max_workers = min(len(selected_children), max(1, int(self._config.max_parallel_branches)))
            branch_results = [None] * len(selected_children)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {
                    executor.submit(
                        self._search_node,
                        model=model,
                        query_messages=query_messages,
                        node=child,
                        depth=depth + 1,
                        top_k=branch_top_k,
                        trace=trace,
                        branch_path=branch_path + (child.node_id,),
                    ): index
                    for index, child in enumerate(selected_children)
                }
                for future, index in future_to_index.items():
                    branch_results[index] = future.result()
        merged: List[FinderCandidate] = []
        for branch_candidates in branch_results:
            if branch_candidates:
                merged.extend(branch_candidates)
        reduced = self._merge_branch_candidates(branch_results=branch_results, top_k=top_k)
        trace.record(
            "reduce_complete",
            node_id=node.node_id,
            depth=depth,
            detail={"input_candidates": len(merged), "output_candidates": len(
                reduced), "mode": "round_robin" if self._config.round_robin_branch_reduce else "sequential"},
        )
        return reduced

    def _select_children(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        children: Sequence[FinderNode],
        depth: int,
        top_k: int,
        trace: FinderTrace,
    ) -> List[FinderNode]:
        if len(children) == 1:
            only_child = children[0]
            trace.record(
                "node_selection",
                node_id=node.node_id,
                depth=depth,
                detail={"selected_node_ids": [only_child.node_id], "raw_output": "", "mode": "single_child_shortcut"},
            )
            return [only_child]
        prompt_node = FinderNode(
            node_id=node.node_id,
            label=node.label,
            description=node.description,
            children=tuple(children),
        )
        options = _build_visible_options(
            branches=children,
            compact_codes_enabled=bool(self._config.compact_boundary_codes_enabled),
        )
        option_by_name = {option.display_name: option for option in options}
        child_by_id = {child.node_id: child for child in children}
        system_prompt = _build_visible_subtree_prompt(
            node=prompt_node,
            options=options,
            top_k=top_k,
            compact_codes_enabled=bool(self._config.compact_boundary_codes_enabled),
        )
        extra = self._build_constraint_extra_body(
            choice_id_to_payload={option.display_name: option.canonical_id for option in options},
            excluded_choice_ids=[],
            top_k=top_k,
        )
        output = self._complete(
            model=model,
            system_prompt=system_prompt,
            query_messages=query_messages,
            max_tokens=self._config.branch_max_tokens,
            trace=trace,
            node_id=node.node_id,
            depth=depth,
            stage="select_children",
            extra_body_override=extra,
            before_llm_call_hook=None,
        )
        selected_ids = self._parse_ids(output)
        selected_children: List[FinderNode] = []
        seen: set[str] = set()
        for display_name in selected_ids:
            option = option_by_name.get(display_name)
            if option is None:
                continue
            node_id = option.canonical_id
            child = child_by_id.get(node_id)
            if child is None or node_id in seen:
                continue
            selected_children.append(child)
            seen.add(node_id)
            if len(selected_children) >= top_k:
                break
        selected_node_ids = [child.node_id for child in selected_children]
        selected_node_id_set = set(selected_node_ids)
        selected_display_names: List[str] = []
        for option in options:
            if option.canonical_id in selected_node_id_set:
                selected_display_names.append(option.display_name)
        trace.record(
            "node_selection",
            node_id=node.node_id,
            depth=depth,
            detail={
                "selected_node_ids": selected_node_ids,
                "selected_display_names": selected_display_names,
                "raw_output": output,
            },
        )
        return selected_children

    def _select_items(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        depth: int,
        top_k: int,
        trace: FinderTrace,
        branch_path: tuple[str, ...],
        items: Sequence[FinderItem] | None = None,
        item_paths: Dict[str, tuple[str, ...]] | None = None,
        allowed_payloads: Dict[str, str] | None = None,
        resolve_candidate: Callable[[str, Dict[str, str]], str] | None = None,
        system_prompt_override: str | None = None,
            before_llm_call_hook: Callable[[], None] | None = None,
    ) -> ProgressiveFinderResult | List[FinderCandidate]:
        candidate_items = list(items if items is not None else node.items)
        resolved_item_paths = dict(item_paths or {})
        visible_options = _build_visible_options(
            items=candidate_items,
            compact_codes_enabled=bool(self._config.compact_boundary_codes_enabled),
        )
        option_by_name = {option.display_name: option for option in visible_options}
        display_name_by_payload = {option.canonical_id: option.display_name for option in visible_options}
        subtree_prompt = _build_visible_subtree_prompt(
            node=node,
            options=visible_options,
            top_k=top_k,
            compact_codes_enabled=bool(self._config.compact_boundary_codes_enabled),
        )
        if not candidate_items:
            return ProgressiveFinderResult(candidates=[], trace=trace) if allowed_payloads is not None else []
        if len(candidate_items) == 1:
            item = candidate_items[0]
            item_branch_path = resolved_item_paths.get(item.item_id, branch_path)
            display_name = display_name_by_payload.get(item.payload or item.item_id, item.payload or item.item_id)
            trace.record(
                "terminal_selection",
                node_id=node.node_id,
                depth=depth,
                detail={"mode": "single_item_shortcut", "selected_item_ids": [
                    item.item_id], "selected_display_names": [display_name]},
            )
            single = FinderCandidate(
                rank=1,
                item_id=item.item_id,
                payload=item.payload,
                branch_path=item_branch_path,
                label=item.label,
                description=item.description)
            if allowed_payloads is not None:
                return ProgressiveFinderResult(
                    candidates=[single],
                    trace=trace,
                    candidate_records=[{"rank": 1,
                                        "raw_output": display_name,
                                        "resolved_payload": item.payload,
                                        "valid": True,
                                        "selected": True,
                                        "choice_id": item.item_id}],
                    summary_lines=[f"1. {display_name} -> {item.payload} (ok, shortcut)"],
                    selected_payload=item.payload,
                    selected_rank=1,
                    raw_outputs=[],
                    request_messages=[],
                )
            return [single]

        if allowed_payloads is not None and resolve_candidate is not None:
            return self._select_items_flat(
                model=model,
                query_messages=query_messages,
                node=node,
                depth=depth,
                top_k=top_k,
                trace=trace,
                branch_path=branch_path,
                choice_id_to_payload=allowed_payloads,
                resolve_candidate=resolve_candidate,
                system_prompt_prefix=str(system_prompt_override or "").strip(),
                before_llm_call_hook=before_llm_call_hook,
                option_by_name=option_by_name,
                item_by_payload={item.payload: item for item in candidate_items},
            )

        system_prompt = subtree_prompt
        extra = self._build_constraint_extra_body(
            choice_id_to_payload={option.display_name: option.canonical_id for option in visible_options},
            excluded_choice_ids=[],
            top_k=top_k,
        )
        output = self._complete(
            model=model,
            system_prompt=system_prompt,
            query_messages=query_messages,
            max_tokens=self._config.item_max_tokens,
            trace=trace,
            node_id=node.node_id,
            depth=depth,
            stage="select_items",
            extra_body_override=extra,
            before_llm_call_hook=None,
        )
        selected_ids = self._parse_ids(output)
        selected: List[FinderCandidate] = []
        seen: set[str] = set()
        item_by_payload = {item.payload: item for item in candidate_items}
        for display_name in selected_ids:
            option = option_by_name.get(display_name)
            if option is None:
                continue
            payload = option.canonical_id
            item = item_by_payload.get(payload)
            if item is None or item.item_id in seen:
                continue
            seen.add(item.item_id)
            selected.append(
                FinderCandidate(
                    rank=len(selected) + 1,
                    item_id=item.item_id,
                    payload=item.payload,
                    branch_path=resolved_item_paths.get(item.item_id, branch_path),
                    label=item.label,
                    description=item.description,
                )
            )
            if len(selected) >= top_k:
                break
        trace.record(
            "terminal_selection",
            node_id=node.node_id,
            depth=depth,
            detail={
                "selected_item_ids": [
                    item.item_id for item in selected],
                "selected_display_names": [
                    display_name_by_payload.get(
                        item.payload,
                        item.item_id) for item in selected],
                "raw_output": output,
            },
        )
        if not selected and _is_abstain_output(output):
            trace.record(
                "terminal_selection_fallback",
                node_id=node.node_id,
                depth=depth,
                detail={"selected_item_ids": [], "strategy": "abstain_no_backfill"},
            )
        return selected

    def _select_items_flat(
        self,
        *,
        model: str,
        query_messages: List[Dict[str, str]],
        node: FinderNode,
        depth: int,
        top_k: int,
        trace: FinderTrace,
        branch_path: tuple[str, ...],
        choice_id_to_payload: Dict[str, str],
        resolve_candidate: Callable[[str, Dict[str, str]], str],
        system_prompt_prefix: str,
        before_llm_call_hook: Callable[[], None] | None,
        option_by_name: Dict[str, _VisibleOption],
        item_by_payload: Dict[str, FinderItem],
    ) -> ProgressiveFinderResult:
        candidate_records: List[Dict[str, object]] = []
        summary_lines: List[str] = []
        raw_outputs: List[str] = []
        selected_payloads: set[str] = set()
        excluded_choice_ids: List[str] = []
        selected_payload: str | None = None
        selected_rank = -1
        global_rank = 0
        max_rounds = max(1, top_k * 2)
        messages: List[Dict[str, str]] = []
        for round_index in range(1, max_rounds + 1):
            remaining = max(0, top_k - len(selected_payloads))
            if remaining <= 0:
                break
            remaining_options: List[SelectableResolution] = []
            for option in option_by_name.values():
                if option.canonical_id not in selected_payloads:
                    remaining_options.append(option)
            if not remaining_options:
                break
            request_k = min(max(1, int(self._config.batch_size)), remaining)
            round_choice_id_to_payload = {
                option.display_name: option.canonical_id
                for option in remaining_options
            }
            extra = self._build_constraint_extra_body(
                choice_id_to_payload=round_choice_id_to_payload,
                excluded_choice_ids=excluded_choice_ids,
                top_k=request_k,
            )
            round_subtree_prompt = _build_visible_subtree_prompt(
                node=node,
                options=remaining_options,
                top_k=request_k,
                compact_codes_enabled=bool(self._config.compact_boundary_codes_enabled),
            )
            system_prompt = f"{system_prompt_prefix}\n\n{round_subtree_prompt}".strip(
            ) if system_prompt_prefix else round_subtree_prompt
            if not messages:
                messages = [{"role": "system", "content": system_prompt}] + list(query_messages)
            output = self._complete(
                model=model,
                system_prompt=system_prompt,
                query_messages=query_messages,
                max_tokens=self._config.max_tokens,
                trace=trace,
                node_id=node.node_id,
                depth=depth,
                stage="select_items",
                extra_body_override=extra,
                before_llm_call_hook=before_llm_call_hook,
                io_event_type="retriever_io",
            )
            if output:
                raw_outputs.append(output)
            parsed_items = self._parse_multi_output(
                output,
                limit=request_k,
                option_by_name=option_by_name,
                item_by_payload=item_by_payload,
            )
            if not output:
                self._record_debug_event({"type": "retriever_iteration",
                                          "model": model,
                                          "round": round_index,
                                          "request_k": request_k,
                                          "remaining": remaining,
                                          "excluded_choice_ids": list(excluded_choice_ids),
                                          "outputs": [],
                                          "new_valid_payloads": [],
                                          "new_excluded_choice_ids": [],
                                          "status": "empty"})
                break
            round_new_valid = 0
            round_new_valid_payloads: List[str] = []
            round_new_excluded_choice_ids: List[str] = []
            for raw_output, resolved_payload, matched_choice_id in parsed_items:
                global_rank += 1
                valid = False
                if resolved_payload and resolved_payload not in selected_payloads:
                    valid = True
                    selected_payloads.add(resolved_payload)
                    if matched_choice_id:
                        excluded_choice_ids.append(matched_choice_id)
                        round_new_excluded_choice_ids.append(matched_choice_id)
                    round_new_valid += 1
                    round_new_valid_payloads.append(resolved_payload)
                    if selected_payload is None:
                        selected_payload = resolved_payload
                        selected_rank = global_rank
                candidate_records.append({"rank": global_rank,
                                          "raw_output": raw_output,
                                          "resolved_payload": resolved_payload,
                                          "valid": valid,
                                          "selected": False,
                                          "choice_id": matched_choice_id})
                label = resolved_payload or "-"
                status = "ok" if valid else "invalid"
                summary_lines.append(f"{global_rank}. {raw_output} -> {label} ({status}, round={round_index})")
            self._record_debug_event({"type": "retriever_iteration",
                                      "model": model,
                                      "round": round_index,
                                      "request_k": request_k,
                                      "remaining": remaining,
                                      "excluded_choice_ids": list(excluded_choice_ids),
                                      "outputs": [item[0] for item in parsed_items],
                                      "raw_output": output,
                                      "new_valid_payloads": round_new_valid_payloads,
                                      "new_excluded_choice_ids": round_new_excluded_choice_ids,
                                      "status": "ok" if round_new_valid > 0 else "stalled"})
            if round_new_valid <= 0:
                break
        if selected_rank > 0 and 0 <= selected_rank - 1 < len(candidate_records):
            candidate_records[selected_rank - 1]["selected"] = True
        candidates = [
            FinderCandidate(
                rank=int(
                    item["rank"]), item_id=str(
                    item.get("choice_id") or item.get("raw_output") or ""), payload=str(
                    item.get("resolved_payload") or ""), branch_path=branch_path, label=str(
                    item.get("choice_id") or ""), description="")
            for item in candidate_records
            if item.get("valid")
        ]
        return ProgressiveFinderResult(
            candidates=candidates,
            trace=trace,
            candidate_records=candidate_records,
            summary_lines=summary_lines,
            selected_payload=selected_payload,
            selected_rank=selected_rank,
            raw_outputs=raw_outputs,
            request_messages=messages,
            elapsed_ms=0.0,
        )

    def _complete(
        self,
        *,
        model: str,
        system_prompt: str,
        query_messages: List[Dict[str, str]],
        max_tokens: int,
        trace: FinderTrace,
        node_id: str,
        depth: int,
        stage: str,
        extra_body_override: Dict[str, Any] | None,
        before_llm_call_hook: Callable[[], None] | None,
        io_event_type: str = "finder_io",
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}] + list(query_messages)
        request_detail = {
            "stage": stage,
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "extra_body": _merged_extra_body(extra_body_override),
        }
        trace.record("llm_request", node_id=node_id, depth=depth, detail=request_detail)
        if self._debug_event_hook is not None:
            self._debug_event_hook({"type": io_event_type, "phase": "request", **request_detail})
        if before_llm_call_hook is not None:
            before_llm_call_hook()
        outputs = self._llm.complete(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stop_sequences=None,
            extra_body_override=extra_body_override,
            n=1,
            request_timeout=self._config.request_timeout,
        )
        output = outputs[0] if outputs else ""
        response_detail = {"stage": stage, "model": model, "outputs": list(outputs)}
        trace.record("llm_response", node_id=node_id, depth=depth, detail=response_detail)
        if self._debug_event_hook is not None:
            self._debug_event_hook({"type": io_event_type, "phase": "response", **response_detail})
        return output

    def _build_constraint_extra_body(
        self,
        *,
        choice_id_to_payload: Dict[str, str],
        excluded_choice_ids: List[str] | None,
        top_k: int,
    ) -> Dict[str, object] | None:
        if not self._config.trie_constrained_decoding_enabled:
            return None
        choice_ids = list(choice_id_to_payload.keys())
        excluded = [str(item).strip() for item in (excluded_choice_ids or []) if str(item).strip()]
        digest = hashlib.sha256(json.dumps(choice_ids, ensure_ascii=False).encode("utf-8")).hexdigest()
        return {
            "vllm_xargs": {
                "constraint_type": "leaf_id_multicid_trie",
                "constraint_version": digest,
                "top_k": max(1, int(top_k)),
                "leaf_ids_json": json.dumps(choice_ids, ensure_ascii=False),
                "excluded_leaf_ids_json": json.dumps(excluded, ensure_ascii=False),
                "allow_user_nodes": self._config.trie_constraint_allow_user_nodes,
                "fallback_cid": self._config.trie_constraint_fallback_payload,
                "max_candidates": self._config.trie_constraint_max_candidates,
            }
        }

    def _collapse_unique_chain(self, node: FinderNode) -> tuple[FinderNode, List[str]]:
        if not self._config.collapse_single_chain:
            return node, []
        current = node
        collapsed: List[str] = []
        steps = 0
        while len(current.children) == 1 and not current.items and steps < max(1, int(self._config.max_collapse_steps)):
            current = current.children[0]
            collapsed.append(current.node_id)
            steps += 1
        return current, collapsed

    @staticmethod
    def _parse_ids(output: str) -> List[str]:
        values: List[str] = []
        for line in str(output or "").splitlines():
            cleaned = re.sub(r"^\s*(?:\d+[\).\s:-]+|[-*]\s+)", "", line.strip())
            if not cleaned:
                continue
            values.append(cleaned.split("|", 1)[0].strip())
        if values:
            return values
        return re.findall(r"[A-Za-z][A-Za-z0-9_./-]*", str(output or ""))

    def _parse_multi_output(
        self,
        content: str,
        *,
        limit: int,
        option_by_name: Dict[str, _VisibleOption],
        item_by_payload: Dict[str, FinderItem],
    ) -> List[tuple[str, str, str]]:
        raw_candidates = self._parse_ids((content or "").strip())
        parsed: List[tuple[str, str, str]] = []
        for raw in raw_candidates:
            option = option_by_name.get(raw)
            if option is None:
                continue
            resolved_payload = option.canonical_id
            item = item_by_payload.get(resolved_payload)
            if item is None:
                continue
            parsed.append((raw, resolved_payload, item.item_id))
            if len(parsed) >= max(1, int(limit)):
                break
        return parsed

    @staticmethod
    def _resolve_choice_id(raw_candidate: str, choice_id_to_payload: Dict[str, str], resolved_payload: str) -> str:
        raw = str(raw_candidate or "").strip().strip("`").strip("<>").strip()
        if raw in choice_id_to_payload and choice_id_to_payload[raw] == resolved_payload:
            return raw
        for choice_id, payload in choice_id_to_payload.items():
            if payload == resolved_payload:
                return choice_id
        return ""

    def _record_debug_event(self, event: Dict[str, object]) -> None:
        if self._debug_event_hook is None:
            return
        self._debug_event_hook(dict(event))

    def _resolve_branch_limit(self, *, child_count: int, top_k: int) -> int:
        return min(max(1, int(child_count)), max(1, min(int(self._config.max_branch_choices),
                                                        int(top_k) + max(0, int(self._config.branch_choice_slack)))), )

    def _resolve_branch_top_k(self, *, top_k: int, branch_count: int) -> int:
        if branch_count <= 0:
            return max(1, int(top_k))
        slack = max(0, int(self._config.branch_candidate_slack))
        budget = math.ceil(max(1, int(top_k)) / branch_count) + slack
        return min(max(1, int(top_k)), max(1, budget))

    def _analyze_node(self, node: FinderNode) -> _FinderNodeStats:
        cache_key = id(node)
        with self._node_stats_lock:
            cached = self._node_stats_cache.get(cache_key)
        if cached is not None:
            return cached
        subtree_item_count = len(node.items)
        subtree_depth = 0
        for child in node.children:
            child_stats = self._analyze_node(child)
            subtree_item_count += child_stats.subtree_item_count
            subtree_depth = max(subtree_depth, child_stats.subtree_depth + 1)
        stats = _FinderNodeStats(subtree_item_count=subtree_item_count, subtree_depth=subtree_depth)
        with self._node_stats_lock:
            self._node_stats_cache[cache_key] = stats
        return stats

    def _collect_subtree_items(
        self,
        node: FinderNode,
        branch_path: tuple[str, ...],
    ) -> List[tuple[FinderItem, tuple[str, ...]]]:
        collected: List[tuple[FinderItem, tuple[str, ...]]] = [(item, branch_path) for item in node.items]
        for child in node.children:
            collected.extend(self._collect_subtree_items(child, branch_path + (child.node_id,)))
        return collected

    def _merge_branch_candidates(
        self,
        *,
        branch_results: Sequence[List[FinderCandidate] | None],
        top_k: int,
    ) -> List[FinderCandidate]:
        if not self._config.round_robin_branch_reduce:
            merged: List[FinderCandidate] = []
            for branch_candidates in branch_results:
                if branch_candidates:
                    merged.extend(branch_candidates)
            return self._dedupe_candidates(merged)[:top_k]
        reduced: List[FinderCandidate] = []
        seen: set[str] = set()
        index = 0
        while len(reduced) < top_k:
            added = False
            for branch_candidates in branch_results:
                if not branch_candidates or index >= len(branch_candidates):
                    continue
                candidate = branch_candidates[index]
                dedupe_key = candidate.payload or candidate.item_id
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                reduced.append(candidate)
                added = True
                if len(reduced) >= top_k:
                    break
            if not added:
                break
            index += 1
        if len(reduced) >= top_k:
            return reduced[:top_k]
        merged: List[FinderCandidate] = []
        for branch_candidates in branch_results:
            if branch_candidates:
                merged.extend(branch_candidates)
        for candidate in self._dedupe_candidates(merged):
            dedupe_key = candidate.payload or candidate.item_id
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            reduced.append(candidate)
            if len(reduced) >= top_k:
                break
        return reduced[:top_k]

    @staticmethod
    def _dedupe_candidates(candidates: List[FinderCandidate]) -> List[FinderCandidate]:
        reduced: List[FinderCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            dedupe_key = candidate.payload or candidate.item_id
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            reduced.append(candidate)
        return reduced


__all__ = [
    "CompletionClient",
    "FinderCandidate",
    "FinderItem",
    "FinderNode",
    "FinderTrace",
    "FinderTraceEvent",
    "RetrieverChoice",
    "ProgressiveFinder",
    "ProgressiveFinderConfig",
    "ProgressiveFinderResult",
]
