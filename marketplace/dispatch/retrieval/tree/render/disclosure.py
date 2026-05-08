from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import yaml

from models.retrieval import RetrieverItem, RetrieverNode
from ...protocols.display_name import to_pascal_case

_BOUNDARY_CODE_ALPHABET = "123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PROMPT_FILE = Path(__file__).with_name("prompts.yaml")


@lru_cache(maxsize=1)
def _load_progressive_prompt_bank() -> dict[str, str]:
    raw = yaml.safe_load(_PROMPT_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"invalid prompt yaml: {_PROMPT_FILE}")
    bank = raw.get("progressive_system_prompt")
    if not isinstance(bank, dict):
        raise ValueError(f"missing progressive_system_prompt in prompt yaml: {_PROMPT_FILE}")
    required_keys = {
        "shared",
        "structure_codes",
        "structure_names",
        "structure_codes_flat",
        "structure_names_flat",
        "output_codes",
        "output_names_tree",
        "output_names_flat",
    }
    missing = sorted(required_keys.difference(bank))
    if missing:
        raise ValueError(f"missing progressive prompt keys in {_PROMPT_FILE}: {missing}")
    return {key: str(value).rstrip("\n") for key, value in bank.items()}


def _build_system_prompt(*, compact_codes_enabled: bool, flat_list_mode: bool, top_k: int) -> str:
    bank = _load_progressive_prompt_bank()
    candidate_region = "<CANDIDATE_LIST>" if flat_list_mode else "<CANDIDATE_TREE>"
    if compact_codes_enabled and flat_list_mode:
        structure_block = bank["structure_codes_flat"]
        output_block = bank["output_codes"]
    elif compact_codes_enabled:
        structure_block = bank["structure_codes"]
        output_block = bank["output_codes"]
    elif flat_list_mode:
        structure_block = bank["structure_names_flat"]
        output_block = bank["output_names_flat"]
    else:
        structure_block = bank["structure_names"]
        output_block = bank["output_names_tree"]
    return bank["shared"].format(
        top_k=max(1, int(top_k)),
        candidate_region=candidate_region,
        structure_block=structure_block,
        output_block=output_block.format(top_k=max(1, int(top_k))),
    )


_SELECTION_LINE_RE = re.compile(r"^\s*(?:\d+[\).\s:-]+|[-*]\s+)?(.+?)\s*$")
_QUERY_FROM_PREFIX_RE = re.compile(r"^\s*From\s+[^:]+:\s*", re.IGNORECASE)
_REPRESENTATIVE_DESCENDANTS_RE = re.compile(
    r"(?:\n\s*|\s+)Representative descendants:\s*.+$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class DisclosureConfig:
    max_exposure_depth_per_call: int = 2
    exposure_threshold: int = 12
    force_expand_single_child: bool = True
    compact_boundary_codes_enabled: bool = False
    compact_boundary_codebook: tuple[str, ...] = ()
    flatten_full_tree_in_prompt: bool = False


@dataclass(frozen=True)
class SelectableResolution:
    code: str
    canonical_id: str
    display_name: str
    label: str
    description: str
    is_terminal: bool
    branch_path: tuple[str, ...]
    score_key: str = ""
    token_id: int | None = None
    node: RetrieverNode | None = None
    item: RetrieverItem | None = None


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
    candidate_codes: tuple[str, ...] = ()
    fragment_fingerprint: str = ""
    code_width: int = 1
    compact_codes_enabled: bool = False
    flat_list_mode: bool = False

    def build_system_prompt(self, *, top_k: int | None = None) -> str:
        resolved_top_k = max(1, int(top_k if top_k is not None else (len(self.code_to_resolution) or 1)))
        base = _build_system_prompt(
            compact_codes_enabled=bool(self.compact_codes_enabled),
            flat_list_mode=bool(self.flat_list_mode),
            top_k=resolved_top_k,
        )
        return base

    @property
    def system_prompt(self) -> str:
        return self.build_system_prompt()

    @property
    def user_prefix(self) -> str:
        if self.flat_list_mode:
            return f"<CANDIDATE_LIST>\n{self.rendered_tree}\n</CANDIDATE_LIST>\n\n<USER_REQUEST>\n"
        return f"<CANDIDATE_TREE>\n{self.rendered_tree}\n</CANDIDATE_TREE>\n\n<USER_REQUEST>\n"


@dataclass(frozen=True)
class DisclosurePromptParts:
    full_messages: tuple[Dict[str, str], ...]
    prefix_messages: tuple[Dict[str, str], ...]
    suffix_text: str
    cache_id: str
    prefix_token_hash: str


def build_exposed_fragment(
    *,
    root: RetrieverNode,
    branch_path: tuple[str, ...],
    config: DisclosureConfig,
    subtree_item_count: callable,
) -> ExposedFragment:
    selectable_entries: List[
        tuple[str, str, str, str, bool, tuple[str, ...], RetrieverNode | None, RetrieverItem | None]
    ] = []
    root_node = _expand_node(
        node=root,
        current_path=branch_path,
        remaining_depth=max(0, int(config.max_exposure_depth_per_call)),
        is_root=True,
        config=config,
        subtree_item_count=subtree_item_count,
        selectable_entries=selectable_entries,
    )
    if bool(config.flatten_full_tree_in_prompt):
        return _build_flat_fragment_from_exposed_subtree(
            root_node=root_node,
            selectable_entries=selectable_entries,
            config=config,
        )
    codes = _build_codes(
        selectable_entries,
        compact_codes_enabled=bool(config.compact_boundary_codes_enabled),
        compact_codebook=config.compact_boundary_codebook,
    )
    display_names = _build_boundary_names(selectable_entries)
    resolution_by_code: Dict[str, SelectableResolution] = {}
    resolution_by_canonical_id: Dict[str, SelectableResolution] = {}
    for code, display_name, entry in zip(codes, display_names, selectable_entries):
        canonical_id, label, description, selectable_canonical_id, is_terminal, selectable_path, node_ref, item_ref = (
            entry
        )
        resolution = SelectableResolution(
            code=code,
            canonical_id=selectable_canonical_id,
            display_name=display_name,
            label=label,
            description=description,
            is_terminal=is_terminal,
            branch_path=selectable_path,
            score_key=code,
            node=node_ref,
            item=item_ref,
        )
        resolution_by_code[code] = resolution
        resolution_by_canonical_id[canonical_id] = resolution
    rendered_tree = _render_tree(
        root_node,
        resolution_by_canonical_id=resolution_by_canonical_id,
        compact_codes_enabled=bool(config.compact_boundary_codes_enabled),
    )
    candidate_codes = tuple(codes)
    return ExposedFragment(
        root=root_node,
        rendered_tree=rendered_tree,
        code_to_resolution=resolution_by_code,
        selectable_nodes=tuple(_iter_selectable_nodes(root_node)),
        candidate_codes=candidate_codes,
        fragment_fingerprint=_build_fragment_fingerprint(root_node=root_node, candidate_codes=candidate_codes),
        code_width=_resolve_code_width(codes),
        compact_codes_enabled=bool(config.compact_boundary_codes_enabled),
        flat_list_mode=False,
    )


def build_disclosure_messages(
    *,
    fragment: ExposedFragment,
    query_messages: Sequence[Dict[str, str]],
    top_k: int | None = None,
) -> List[Dict[str, str]]:
    return list(
        build_disclosure_prompt_parts(
            fragment=fragment,
            query_messages=query_messages,
            top_k=top_k,
        ).full_messages
    )


def build_disclosure_prompt_parts(
    *,
    fragment: ExposedFragment,
    query_messages: Sequence[Dict[str, str]],
    top_k: int | None = None,
) -> DisclosurePromptParts:
    query_text = "\n".join(
        _normalize_query_content(str(message.get("content") or "").strip())
        for message in query_messages
        if str(message.get("content") or "").strip()
    ).strip()
    resolved_top_k = None if top_k is None else max(1, int(top_k))
    system_prompt, user_prefix, prefix_token_hash, cache_id = _build_disclosure_prompt_static(
        rendered_tree=fragment.rendered_tree,
        fragment_fingerprint=fragment.fragment_fingerprint,
        candidate_codes=tuple(str(code) for code in fragment.candidate_codes),
        compact_codes_enabled=bool(fragment.compact_codes_enabled),
        flat_list_mode=bool(fragment.flat_list_mode),
        top_k=resolved_top_k,
    )
    suffix_text = f"{query_text}\n</USER_REQUEST>".rstrip()
    user_content = f"{user_prefix}{suffix_text}".rstrip()
    prefix_messages = (
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prefix},
    )
    full_messages = (
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    )
    return DisclosurePromptParts(
        full_messages=full_messages,
        prefix_messages=prefix_messages,
        suffix_text=suffix_text,
        cache_id=cache_id,
        prefix_token_hash=prefix_token_hash,
    )


@lru_cache(maxsize=4096)
def _build_disclosure_prompt_static(
    *,
    rendered_tree: str,
    fragment_fingerprint: str,
    candidate_codes: tuple[str, ...],
    compact_codes_enabled: bool,
    flat_list_mode: bool,
    top_k: int | None,
) -> tuple[str, str, str, str]:
    system_prompt = _build_system_prompt(
        compact_codes_enabled=bool(compact_codes_enabled),
        flat_list_mode=bool(flat_list_mode),
        top_k=max(1, int(top_k if top_k is not None else (len(candidate_codes) or 1))),
    )
    if flat_list_mode:
        user_prefix = f"<CANDIDATE_LIST>\n{rendered_tree}\n</CANDIDATE_LIST>\n\n<USER_REQUEST>\n"
    else:
        user_prefix = f"<CANDIDATE_TREE>\n{rendered_tree}\n</CANDIDATE_TREE>\n\n<USER_REQUEST>\n"
    prefix_payload = (
        f"{system_prompt}\n"
        f"{user_prefix}\n"
        f"{fragment_fingerprint}\n"
        f"{top_k or ''}\n"
        f"{int(compact_codes_enabled)}\n"
        f"{int(flat_list_mode)}"
    )
    prefix_token_hash = hashlib.sha256(prefix_payload.encode("utf-8")).hexdigest()
    cache_id = hashlib.sha256(
        (
            "progressive-disclosure-v1\n" f"{prefix_token_hash}\n" f"{'/'.join(str(code) for code in candidate_codes)}"
        ).encode("utf-8")
    ).hexdigest()[:32]
    return system_prompt, user_prefix, prefix_token_hash, cache_id


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
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = _SELECTION_LINE_RE.match(raw)
        if match:
            lines.append(match.group(1).strip())
    if fragment.compact_codes_enabled:
        parsed_codes = [line for line in lines if line in fragment.code_to_resolution]
    else:
        parsed_codes = [line for line in lines if line]
    compact_output = "\n" not in text and " " not in text
    compact_parse_enabled = fragment.compact_codes_enabled and fragment.code_width > 0
    if not parsed_codes and compact_parse_enabled and compact_output:
        compact = text.strip()
        if compact != "0" and len(compact) % fragment.code_width == 0:
            parsed_codes = [
                compact[index:index + fragment.code_width] for index in range(0, len(compact), fragment.code_width)
            ]
    selected: List[SelectableResolution] = []
    seen: set[str] = set()
    for code in parsed_codes:
        resolution = fragment.code_to_resolution.get(code)
        if resolution is None and not fragment.compact_codes_enabled:
            resolution = _match_numeric_code(fragment=fragment, code=code)
        if resolution is None and not fragment.compact_codes_enabled:
            resolution = _match_label_code(fragment=fragment, code=code)
        if resolution is None:
            continue
        dedupe_key = resolution.canonical_id
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        selected.append(resolution)
    return selected


def _match_numeric_code(*, fragment: ExposedFragment, code: str) -> SelectableResolution | None:
    text = str(code or "").strip()
    if not text.isdigit():
        return None
    numeric_value = str(int(text))
    for candidate, resolution in fragment.code_to_resolution.items():
        candidate_text = str(candidate or "").strip()
        if not candidate_text.isdigit():
            continue
        if str(int(candidate_text)) == numeric_value:
            return resolution
    index = int(numeric_value) - 1
    ordered = list(fragment.code_to_resolution.values())
    if 0 <= index < len(ordered):
        return ordered[index]
    return None


def _match_label_code(*, fragment: ExposedFragment, code: str) -> SelectableResolution | None:
    text = str(code or "").strip()
    if not text:
        return None
    matches = []
    for resolution in fragment.code_to_resolution.values():
        if str(resolution.label or "").strip() == text:
            matches.append(resolution)
    if len(matches) == 1:
        return matches[0]
    return None


def _expand_node(
    *,
    node: RetrieverNode,
    current_path: tuple[str, ...],
    remaining_depth: int,
    is_root: bool,
    config: DisclosureConfig,
    subtree_item_count: callable,
    selectable_entries: List[
        tuple[str, str, str, str, bool, tuple[str, ...], RetrieverNode | None, RetrieverItem | None]
    ],
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
    can_force_single_child = bool(config.force_expand_single_child) and remaining_depth > 0
    has_only_branch_child = not node.items and len(child_nodes) == 1
    if not is_root and can_force_single_child and has_only_branch_child:
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


def _build_flat_fragment_from_exposed_subtree(
    *,
    root_node: ExposedNode,
    selectable_entries: Sequence[
        tuple[str, str, str, str, bool, tuple[str, ...], RetrieverNode | None, RetrieverItem | None]
    ],
    config: DisclosureConfig,
) -> ExposedFragment:
    codes = _build_codes(
        selectable_entries,
        compact_codes_enabled=bool(config.compact_boundary_codes_enabled),
        compact_codebook=config.compact_boundary_codebook,
    )
    display_names = _build_boundary_names(selectable_entries)
    resolution_by_code: Dict[str, SelectableResolution] = {}
    resolution_by_canonical_id: Dict[str, SelectableResolution] = {}
    selectable_nodes: List[ExposedNode] = []
    for code, display_name, entry in zip(codes, display_names, selectable_entries):
        canonical_id, label, description, selectable_canonical_id, is_terminal, selectable_path, node_ref, item_ref = (
            entry
        )
        resolution = SelectableResolution(
            code=code,
            canonical_id=selectable_canonical_id,
            display_name=display_name,
            label=label,
            description=description,
            is_terminal=is_terminal,
            branch_path=selectable_path,
            score_key=code,
            node=node_ref,
            item=item_ref,
        )
        resolution_by_code[code] = resolution
        resolution_by_canonical_id[canonical_id] = resolution
        selectable_nodes.append(
            ExposedNode(
                canonical_id=canonical_id,
                label=label,
                description=description,
                is_selectable=True,
                selectable_canonical_id=selectable_canonical_id,
            )
        )
    flat_root_node = ExposedNode(
        canonical_id=root_node.canonical_id,
        label=root_node.label,
        description=root_node.description,
        is_selectable=False,
        children=tuple(selectable_nodes),
    )
    rendered_tree = _render_flat_list(
        selectable_nodes,
        resolution_by_canonical_id=resolution_by_canonical_id,
        compact_codes_enabled=bool(config.compact_boundary_codes_enabled),
    )
    candidate_codes = tuple(codes)
    return ExposedFragment(
        root=flat_root_node,
        rendered_tree=rendered_tree,
        code_to_resolution=resolution_by_code,
        selectable_nodes=tuple(selectable_nodes),
        candidate_codes=candidate_codes,
        fragment_fingerprint=_build_fragment_fingerprint(root_node=flat_root_node, candidate_codes=candidate_codes),
        code_width=_resolve_code_width(codes),
        compact_codes_enabled=bool(config.compact_boundary_codes_enabled),
        flat_list_mode=True,
    )


def _register_selectable_branch(
    *,
    child: RetrieverNode,
    child_path: tuple[str, ...],
    selectable_entries: List[
        tuple[str, str, str, str, bool, tuple[str, ...], RetrieverNode | None, RetrieverItem | None]
    ],
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
    compact_codes_enabled: bool,
    depth: int = 0,
) -> str:
    lines: List[str] = []
    indent = "  " * depth
    if node.is_selectable:
        resolution = resolution_by_canonical_id[node.canonical_id]
        if compact_codes_enabled:
            identifier = f"Candidate {resolution.code} | {resolution.display_name}"
        else:
            identifier = f"Candidate {resolution.display_name}"
    else:
        identifier = f"Category {node.label}"
    description = str(node.description or "")
    if node.is_selectable:
        description = _sanitize_candidate_description(description)
    if description:
        lines.append(f"{indent}- {identifier}: {description}")
    else:
        lines.append(f"{indent}- {identifier}")
    for child in node.children:
        lines.append(
            _render_tree(
                child,
                resolution_by_canonical_id=resolution_by_canonical_id,
                compact_codes_enabled=compact_codes_enabled,
                depth=depth + 1,
            )
        )
    return "\n".join(lines)


def _render_flat_list(
    selectable_nodes: Sequence[ExposedNode],
    *,
    resolution_by_canonical_id: Dict[str, SelectableResolution],
    compact_codes_enabled: bool,
) -> str:
    lines: List[str] = []
    for node in selectable_nodes:
        resolution = resolution_by_canonical_id[node.canonical_id]
        if compact_codes_enabled:
            identifier = f"{resolution.code} | {resolution.display_name}"
        else:
            identifier = f"{resolution.display_name}"
        description = _sanitize_candidate_description(str(node.description or ""))
        if description:
            lines.append(f"- {identifier}: {description}")
        else:
            lines.append(f"- {identifier}")
    return "\n".join(lines)


def _sanitize_candidate_description(description: str) -> str:
    text = str(description or "").strip()
    if not text:
        return ""
    text = _REPRESENTATIVE_DESCENDANTS_RE.sub("", text).strip()
    return text


def _iter_selectable_nodes(node: ExposedNode) -> Iterable[ExposedNode]:
    if node.is_selectable:
        yield node
    for child in node.children:
        yield from _iter_selectable_nodes(child)


def _build_codes(
    selectable_entries: Sequence[
        tuple[str, str, str, str, bool, tuple[str, ...], RetrieverNode | None, RetrieverItem | None]
    ],
    *,
    compact_codes_enabled: bool,
    compact_codebook: Sequence[str] = (),
) -> List[str]:
    count = len(selectable_entries)
    if count <= 0:
        return []
    if not compact_codes_enabled:
        return _build_boundary_names(selectable_entries)
    normalized_codebook = _normalize_codebook(compact_codebook)
    if normalized_codebook:
        if len(normalized_codebook) < count:
            raise ValueError(
                f"compact codebook provides {len(normalized_codebook)} codes, but {count} selectable nodes were exposed"
            )
        return list(normalized_codebook[:count])
    base = len(_BOUNDARY_CODE_ALPHABET)
    width = 1
    capacity = base
    while count > capacity:
        width += 1
        capacity *= base
    return [_encode_boundary_code(index, width=width) for index in range(count)]


def _build_boundary_names(
    selectable_entries: Sequence[
        tuple[str, str, str, str, bool, tuple[str, ...], RetrieverNode | None, RetrieverItem | None]
    ],
) -> List[str]:
    base_names: List[str] = []
    for entry in selectable_entries:
        selectable_canonical_id = str(entry[3] or "").strip()
        terminal = selectable_canonical_id.split(".")[-1] if selectable_canonical_id else ""
        base_name = (
            to_pascal_case(terminal)
            or to_pascal_case(selectable_canonical_id.replace(".", "-"))
            or selectable_canonical_id
            or str(entry[1] or "").strip()
        )
        base_names.append(base_name)
    grouped: Dict[str, List[int]] = {}
    for index, name in enumerate(base_names):
        grouped.setdefault(name, []).append(index)

    identifiers = list(base_names)
    for name, indices in grouped.items():
        if len(indices) <= 1:
            continue
        for index in indices:
            selectable_path = tuple(
                str(part or "").strip() for part in selectable_entries[index][5] if str(part or "").strip()
            )
            path_parts = [to_pascal_case(part) or part for part in selectable_path[1:] if part]
            if path_parts:
                identifiers[index] = "/".join([*path_parts, name])
            else:
                identifiers[index] = f"{name}__{index + 1}"
    return identifiers


def _normalize_codebook(codebook: Sequence[str]) -> tuple[str, ...]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw_code in codebook:
        code = str(raw_code or "").strip()
        if not code:
            raise ValueError("compact codebook entries must be non-empty strings")
        if code == "0":
            raise ValueError("compact codebook cannot include reserved abstain code '0'")
        if any(character.isspace() for character in code):
            raise ValueError(f"compact codebook entry {code!r} cannot contain whitespace")
        if code in seen:
            raise ValueError(f"compact codebook entry {code!r} is duplicated")
        seen.add(code)
        normalized.append(code)
    return tuple(normalized)


def _resolve_code_width(codes: Sequence[str]) -> int:
    if not codes:
        return 1
    widths = {len(str(code)) for code in codes}
    if len(widths) == 1:
        return next(iter(widths))
    return 0


def _build_fragment_fingerprint(*, root_node: ExposedNode, candidate_codes: Sequence[str]) -> str:
    payload = f"{_serialize_exposed_node(root_node)}|{'/'.join(str(code) for code in candidate_codes)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _serialize_exposed_node(node: ExposedNode) -> str:
    children = ",".join(_serialize_exposed_node(child) for child in node.children)
    return f"{node.canonical_id}|{node.label}|{int(node.is_selectable)}|{children}"


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
    "DisclosurePromptParts",
    "ExposedFragment",
    "ExposedNode",
    "SelectableResolution",
    "build_disclosure_messages",
    "build_disclosure_prompt_parts",
    "build_exposed_fragment",
    "parse_selected_codes",
]
