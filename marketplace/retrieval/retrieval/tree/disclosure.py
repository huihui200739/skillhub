from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence

from models.retrieval import FinderItem, FinderNode

_BOUNDARY_CODE_ALPHABET = "123456789ABCDEFGHJKMNPQRSTVWXYZ"

_SYSTEM_PROMPT = "\n".join(
    [
        "You are selecting relevant nodes from a partially exposed capability tree.",
        "Any node with a code is selectable.",
        "Return only codes, one per line.",
        "Do not explain.",
        "If nothing is relevant, return 0.",
    ]
)

_SELECTION_LINE_RE = re.compile(r"^\s*(?:\d+[\).\s:-]+|[-*]\s+)?(.+?)\s*$")
_QUERY_FROM_PREFIX_RE = re.compile(r"^\s*From\s+[^:]+:\s*", re.IGNORECASE)


@dataclass(frozen=True)
class DisclosureConfig:
    max_exposure_depth_per_call: int = 2
    exposure_threshold: int = 12
    force_expand_single_child: bool = True
    compact_boundary_codes_enabled: bool = False


@dataclass(frozen=True)
class SelectableResolution:
    code: str
    canonical_id: str
    label: str
    description: str
    is_terminal: bool
    branch_path: tuple[str, ...]
    node: FinderNode | None = None
    item: FinderItem | None = None


@dataclass(frozen=True)
class ExposedNode:
    canonical_id: str
    label: str
    description: str
    is_selectable: bool
    selectable_canonical_id: str | None = None
    children: tuple["ExposedNode", ...] = ()


@dataclass(frozen=True)
class ExposedFragment:
    root: ExposedNode
    rendered_tree: str
    code_to_resolution: Dict[str, SelectableResolution]
    selectable_nodes: tuple[ExposedNode, ...] = ()
    code_width: int = 1

    @property
    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    @property
    def user_prefix(self) -> str:
        return f"Visible tree:\n{self.rendered_tree}\n\nUser query:\n"


def build_exposed_fragment(
    *,
    root: FinderNode,
    branch_path: tuple[str, ...],
    config: DisclosureConfig,
    subtree_item_count: callable,
) -> ExposedFragment:
    selectable_entries: List[tuple[str, str, str, str, bool,
                                   tuple[str, ...], FinderNode | None, FinderItem | None]] = []
    root_node = _expand_node(
        node=root,
        current_path=branch_path,
        remaining_depth=max(0, int(config.max_exposure_depth_per_call)),
        is_root=True,
        config=config,
        subtree_item_count=subtree_item_count,
        selectable_entries=selectable_entries,
    )
    codes = _build_codes(len(selectable_entries), compact_codes_enabled=bool(config.compact_boundary_codes_enabled))
    resolution_by_code: Dict[str, SelectableResolution] = {}
    resolution_by_canonical_id: Dict[str, SelectableResolution] = {}
    for code, entry in zip(codes, selectable_entries):
        (
            canonical_id,
            label,
            description,
            selectable_canonical_id,
            is_terminal,
            selectable_path,
            node_ref,
            item_ref,
        ) = entry
        resolution = SelectableResolution(
            code=code,
            canonical_id=selectable_canonical_id,
            label=label,
            description=description,
            is_terminal=is_terminal,
            branch_path=selectable_path,
            node=node_ref,
            item=item_ref,
        )
        resolution_by_code[code] = resolution
        resolution_by_canonical_id[canonical_id] = resolution
    rendered_tree = _render_tree(root_node, resolution_by_canonical_id=resolution_by_canonical_id)
    return ExposedFragment(
        root=root_node,
        rendered_tree=rendered_tree,
        code_to_resolution=resolution_by_code,
        selectable_nodes=tuple(_iter_selectable_nodes(root_node)),
        code_width=(len(codes[0]) if codes else 1),
    )


def build_disclosure_messages(
    *,
    fragment: ExposedFragment,
    query_messages: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    query_text = "\n".join(
        _normalize_query_content(str(message.get("content") or "").strip())
        for message in query_messages
        if str(message.get("content") or "").strip()
    ).strip()
    user_content = f"{fragment.user_prefix}{query_text}".rstrip()
    return [
        {"role": "system", "content": fragment.system_prompt},
        {"role": "user", "content": user_content},
    ]


def _normalize_query_content(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        cleaned = _QUERY_FROM_PREFIX_RE.sub("", raw.strip())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def parse_selected_codes(*, fragment: ExposedFragment, output: str) -> List[SelectableResolution]:
    text = str(output or "").strip()
    if not text or text == "0":
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = _SELECTION_LINE_RE.match(raw)
        if match:
            lines.append(match.group(1).strip())
    parsed_codes = [line.split("|", 1)[0].strip() for line in lines if line.split("|", 1)[0].strip()]
    compact_candidate = fragment.code_width > 0 and "\n" not in text and " " not in text
    if not parsed_codes and compact_candidate:
        compact = text.strip()
        if compact != "0" and len(compact) % fragment.code_width == 0:
            parsed_codes = [compact[index: index + fragment.code_width]
                            for index in range(0, len(compact), fragment.code_width)]
    selected: List[SelectableResolution] = []
    seen: set[str] = set()
    for code in parsed_codes:
        resolution = fragment.code_to_resolution.get(code)
        if resolution is None:
            continue
        dedupe_key = resolution.canonical_id
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        selected.append(resolution)
    return selected


def _expand_node(
    *,
    node: FinderNode,
    current_path: tuple[str, ...],
    remaining_depth: int,
    is_root: bool,
    config: DisclosureConfig,
    subtree_item_count: callable,
    selectable_entries: List[tuple[str, str, str, str, bool, tuple[str, ...], FinderNode | None, FinderItem | None]],
) -> ExposedNode:
    children_nodes: List[ExposedNode] = []
    for item in node.items:
        canonical_id = f"item::{item.payload or item.item_id}"
        selectable_entries.append(
            (
                canonical_id,
                item.label or item.item_id,
                item.description or "",
                item.payload or item.item_id,
                True,
                current_path,
                None,
                item,
            )
        )
        children_nodes.append(
            ExposedNode(
                canonical_id=canonical_id,
                label=item.label or item.item_id,
                description=item.description or "",
                is_selectable=True,
                selectable_canonical_id=item.payload or item.item_id,
            )
        )

    child_nodes = list(node.children)
    should_force_expand = (
        not is_root
        and bool(config.force_expand_single_child)
        and remaining_depth > 0
        and not node.items
    )
    if should_force_expand and len(child_nodes) == 1:
        only_child = child_nodes[0]
        children_nodes.append(
            _expand_node(
                node=only_child,
                current_path=current_path + (only_child.node_id,),
                remaining_depth=remaining_depth - 1,
                is_root=False,
                config=config,
                subtree_item_count=subtree_item_count,
                selectable_entries=selectable_entries,
            )
        )
        return ExposedNode(
            canonical_id=f"node::{node.node_id}",
            label=node.label or node.node_id,
            description=node.description or "",
            is_selectable=False,
            children=tuple(children_nodes),
        )

    for child in child_nodes:
        child_path = current_path + (child.node_id,)
        if remaining_depth <= 0:
            children_nodes.append(
                _register_selectable_branch(
                    child=child,
                    child_path=child_path,
                    selectable_entries=selectable_entries,
                )
            )
            continue
        child_item_count = int(subtree_item_count(child))
        if child_item_count <= max(0, int(config.exposure_threshold)):
            children_nodes.append(
                _expand_node(
                    node=child,
                    current_path=child_path,
                    remaining_depth=remaining_depth - 1,
                    is_root=False,
                    config=config,
                    subtree_item_count=subtree_item_count,
                    selectable_entries=selectable_entries,
                )
            )
        else:
            children_nodes.append(
                _register_selectable_branch(
                    child=child,
                    child_path=child_path,
                    selectable_entries=selectable_entries,
                )
            )
    return ExposedNode(
        canonical_id=f"node::{node.node_id}",
        label=node.label or node.node_id,
        description=node.description or "",
        is_selectable=False,
        children=tuple(children_nodes),
    )


def _register_selectable_branch(
    *,
    child: FinderNode,
    child_path: tuple[str, ...],
    selectable_entries: List[tuple[str, str, str, str, bool, tuple[str, ...], FinderNode | None, FinderItem | None]],
) -> ExposedNode:
    canonical_id = f"node::{child.node_id}"
    selectable_entries.append(
        (
            canonical_id,
            child.label or child.node_id,
            child.description or "",
            child.node_id,
            False,
            child_path,
            child,
            None,
        )
    )
    return ExposedNode(
        canonical_id=canonical_id,
        label=child.label or child.node_id,
        description=child.description or "",
        is_selectable=True,
        selectable_canonical_id=child.node_id,
    )


def _render_tree(
    node: ExposedNode,
    *,
    resolution_by_canonical_id: Dict[str, SelectableResolution],
    depth: int = 0,
) -> str:
    lines: List[str] = []
    indent = "  " * depth
    if node.is_selectable:
        resolution = resolution_by_canonical_id[node.canonical_id]
        line = f"{indent}- {resolution.code} | {node.label}"
    else:
        line = f"{indent}- {node.label}"
    if node.description:
        line = f"{line} | {node.description}"
    lines.append(line)
    for child in node.children:
        lines.append(
            _render_tree(
                child,
                resolution_by_canonical_id=resolution_by_canonical_id,
                depth=depth + 1,
            )
        )
    return "\n".join(lines)


def _iter_selectable_nodes(node: ExposedNode) -> Iterable[ExposedNode]:
    if node.is_selectable:
        yield node
    for child in node.children:
        yield from _iter_selectable_nodes(child)


def _build_codes(count: int, *, compact_codes_enabled: bool) -> List[str]:
    if count <= 0:
        return []
    if not compact_codes_enabled:
        width = len(str(count))
        return [str(index).zfill(width) for index in range(1, count + 1)]
    base = len(_BOUNDARY_CODE_ALPHABET)
    width = 1
    capacity = base
    while count > capacity:
        width += 1
        capacity *= base
    return [_encode_boundary_code(index, width=width) for index in range(count)]


def _encode_boundary_code(index: int, *, width: int) -> str:
    base = len(_BOUNDARY_CODE_ALPHABET)
    value = max(0, int(index))
    encoded: List[str] = []
    for _ in range(width):
        encoded.append(_BOUNDARY_CODE_ALPHABET[value % base])
        value //= base
    return "".join(reversed(encoded))


__all__ = [
    "DisclosureConfig",
    "ExposedFragment",
    "ExposedNode",
    "SelectableResolution",
    "build_disclosure_messages",
    "build_exposed_fragment",
    "parse_selected_codes",
]
