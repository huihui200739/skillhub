"""Core schema and config objects for Demo's capability-tree indexing flow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union


class SkillStatus(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    PINNED = "pinned"

    @classmethod
    def default(cls) -> "SkillStatus":
        return cls.ACTIVE


SKILL_DESCRIPTION_MAX_LENGTH = 150

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"
DEFAULT_TREE_OUTPUT_PATH = PROJECT_ROOT / "data" / "capability_trees" / "tree.yaml"

BRANCHING_FACTOR = 8
MAX_DEPTH = 6

TREE_BUILD_MAX_WORKERS = 1
TREE_BUILD_CACHING = False
TREE_BUILD_NUM_RETRIES = 2
TREE_BUILD_TIMEOUT = 180.0
TREE_BUILD_CONTEXT_WINDOW = 0
TREE_BUILD_MAX_OUTPUT_TOKENS = 0
TREE_BUILD_POSTPROCESS_ENABLED = True
TREE_BUILD_POSTPROCESS_MAX_PASSES = 1
TREE_BUILD_POSTPROCESS_MIN_SKILLS = 6
TREE_BUILD_EQUIV_GROUPING_ENABLED = True
TREE_BUILD_EQUIV_MAX_GROUPS_PER_PARENT = 6
TREE_BUILD_EQUIV_ALLOW_SINGLETON_GROUPS = True
TREE_BUILD_EQUIV_MIN_LEXICAL_SIMILARITY = 0.12

MAX_SKILLS_PER_NODE_MULTIPLIER = 1.5
EXPAND_THRESHOLD_MULTIPLIER = 0.7
EARLY_STOP_MULTIPLIER = 1.7
LAZY_SPLIT_MULTIPLIER = 1.3
CLASSIFICATION_BATCH_MULTIPLIER = 6
STRUCTURE_SAMPLE_MULTIPLIER = 12

FIXED_ROOT_CATEGORIES = {
    "software-development": {
        "name": "Software Development",
        "description": "Programming, code generation, debugging, testing, deployment, and engineering workflow skills."
    },
    "office-productivity": {
        "name": "Office Productivity",
        "description": (
            "Document processing, spreadsheets, presentations, workflow automation, "
            "and general workplace productivity skills."
        ),
    },
    "content-creation": {
        "name": "Content Creation",
        "description": (
            "Writing, editing, copywriting, script drafting, publishing support, "
            "and creative content production skills."
        ),
    },
    "multimodal-media": {
        "name": "Multimodal & Media",
        "description": "Image, audio, video, design, and multimodal generation or understanding skills."
    },
    "data-science-research": {
        "name": "Data Science & Research",
        "description": (
            "Data analysis, statistics, experimentation, scientific research, "
            "literature review, and insight generation skills."
        ),
    },
    "compliance-legal": {
        "name": "Compliance & Legal",
        "description": (
            "Policy interpretation, compliance checks, risk control, legal drafting support, "
            "and regulatory analysis skills."
        ),
    },
    "lifestyle-health": {
        "name": "Lifestyle & Health",
        "description": (
            "Daily-life assistance, wellness guidance, health-related information support, "
            "and practical utility skills."
        ),
    },
    "finance-wealth": {
        "name": "Finance & Wealth Management",
        "description": (
            "Financial analysis, personal finance planning, budgeting, investment support, "
            "and wealth management skills."
        ),
    },
}


def _slug_term(value: str, fallback: str = "category") -> str:
    source = str(value or "")
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", source).replace("_", "-").lower()
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-") or fallback
    return normalized if normalized[0].isalpha() else f"n-{normalized}"


def normalize_root_categories(raw_categories) -> Optional[dict]:
    if raw_categories in (None, [], ()):
        return None
    if not isinstance(raw_categories, list):
        raise ValueError("TREE_BUILDER_ROOT_CATEGORIES must be a list.")

    items: list[tuple[str, dict[str, str]]] = []
    for entry in raw_categories:
        if isinstance(entry, str):
            label = entry.strip()
            if not label:
                continue
            category_id = _slug_term(label)
            payload = {"name": label, "description": f"Skills related to {label.lower()}."}
        elif isinstance(entry, dict):
            label = str(entry.get("name") or entry.get("id") or "").strip()
            if not label:
                raise ValueError("Each TREE_BUILDER_ROOT_CATEGORIES item must include 'name' or 'id'.")
            category_id = _slug_term(str(entry.get("id") or label))
            payload = {
                "name": label,
                "description": str(entry.get("description") or f"Skills related to {label.lower()}.").strip(),
            }
        else:
            raise ValueError("TREE_BUILDER_ROOT_CATEGORIES items must be strings or dicts.")
        items.append((category_id, payload))

    normalized: dict[str, dict[str, str]] = {}
    for category_id, payload in items:
        if category_id in normalized:
            raise ValueError(f"Duplicate TREE_BUILDER_ROOT_CATEGORIES id: {category_id}")
        normalized[category_id] = payload
    return normalized or None


@dataclass(frozen=True)
class TreeBuildConfig:
    max_workers: int = TREE_BUILD_MAX_WORKERS
    caching: bool = TREE_BUILD_CACHING
    num_retries: int = TREE_BUILD_NUM_RETRIES
    timeout: float = TREE_BUILD_TIMEOUT
    context_window: int = TREE_BUILD_CONTEXT_WINDOW
    max_output_tokens: int = TREE_BUILD_MAX_OUTPUT_TOKENS
    postprocess_enabled: bool = TREE_BUILD_POSTPROCESS_ENABLED
    postprocess_max_passes: int = TREE_BUILD_POSTPROCESS_MAX_PASSES
    postprocess_min_skills: int = TREE_BUILD_POSTPROCESS_MIN_SKILLS
    equiv_grouping_enabled: bool = TREE_BUILD_EQUIV_GROUPING_ENABLED
    equiv_max_groups_per_parent: int = TREE_BUILD_EQUIV_MAX_GROUPS_PER_PARENT
    equiv_allow_singleton_groups: bool = TREE_BUILD_EQUIV_ALLOW_SINGLETON_GROUPS
    equiv_min_lexical_similarity: float = TREE_BUILD_EQUIV_MIN_LEXICAL_SIMILARITY
    deterministic_prompts: bool = True
    discovery_seed: int = 42
    prompt_fingerprint_version: str = "v1"
    cache_observability: bool = True


@dataclass(frozen=True)
class TreeManagerConfig:
    branching_factor: int = BRANCHING_FACTOR
    max_depth: int = MAX_DEPTH
    root_categories: Optional[dict] = None
    build: TreeBuildConfig = field(default_factory=TreeBuildConfig)


@dataclass
class DynamicTreeConfig:
    branching_factor: int = BRANCHING_FACTOR
    max_depth: int = MAX_DEPTH
    root_categories: Optional[dict] = None
    rebalance_interval: int = 50

    def _scaled(self, multiplier: float, seed: Optional[int] = None) -> int:
        anchor = self.branching_factor if seed is None else seed
        return int(anchor * multiplier)

    def _derived_value(self, key: str) -> int:
        if key == "max_skills_per_node":
            return self._scaled(MAX_SKILLS_PER_NODE_MULTIPLIER)
        if key == "expand_threshold":
            return self._scaled(EXPAND_THRESHOLD_MULTIPLIER)
        if key == "early_stop_skill_count":
            return self._scaled(EARLY_STOP_MULTIPLIER)
        if key == "lazy_split_threshold":
            return self._scaled(LAZY_SPLIT_MULTIPLIER, self.max_skills_per_node)
        if key == "classification_batch_size":
            return self._scaled(CLASSIFICATION_BATCH_MULTIPLIER)
        if key == "structure_sample_size":
            return self._scaled(STRUCTURE_SAMPLE_MULTIPLIER)
        raise KeyError(key)

    @property
    def max_skills_per_node(self) -> int:
        return self._derived_value("max_skills_per_node")

    @property
    def expand_threshold(self) -> int:
        return self._derived_value("expand_threshold")

    @property
    def early_stop_skill_count(self) -> int:
        return self._derived_value("early_stop_skill_count")

    @property
    def lazy_split_threshold(self) -> int:
        return self._derived_value("lazy_split_threshold")

    @property
    def classification_batch_size(self) -> int:
        return self._derived_value("classification_batch_size")

    @property
    def structure_sample_size(self) -> int:
        return self._derived_value("structure_sample_size")


class Skill:
    def __init__(
        self,
        *,
        skill_id: str,
        name: str,
        description: str = "",
        path: str = "",
        skill_path: str = "",
        content: str = "",
        selection_reason: str = "",
        github_url: str = "",
        stars: int = 0,
        is_official: bool = False,
        author: str = "",
        status: SkillStatus = SkillStatus.ACTIVE,
        installs_count: int = 0,
        pinned_at: Optional[str] = None,
        last_used: Optional[str] = None,
    ) -> None:
        self.id = str(skill_id)
        self.name = name
        self.description = description
        self.path = path
        self.skill_path = skill_path
        self.content = content
        self.selection_reason = selection_reason
        self.github_url = github_url
        self.stars = stars
        self.is_official = is_official
        self.author = author
        self.status = status
        self.installs_count = installs_count
        self.pinned_at = pinned_at
        self.last_used = last_used

    def to_dict(self, include_content: bool = True) -> dict:
        keys = ("id", "name", "description", "skill_path", "github_url", "stars", "is_official", "author")
        values = (
            self.id,
            self.name,
            self.description,
            self.skill_path,
            self.github_url,
            self.stars,
            self.is_official,
            self.author,
        )
        payload = dict(zip(keys, values, strict=False))
        if include_content:
            payload["content"] = self.content
        return payload


class TreeNode:
    def __init__(
        self,
        *,
        node_id: str,
        name: str,
        description: str = "",
        children: Optional[list["TreeNode"]] = None,
        skills: Optional[list[Skill]] = None,
        depth: int = 0,
        parent_id: Optional[str] = None,
        pending_split: bool = False,
    ) -> None:
        self.id = str(node_id)
        self.name = name
        self.description = description
        self.children = list(children or [])
        self.skills = list(skills or [])
        self.depth = depth
        self.parent_id = parent_id
        self.pending_split = pending_split

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def is_intermediate(self) -> bool:
        return not self.is_leaf

    def count_all_skills(self) -> int:
        total = 0
        stack = [self]
        while stack:
            current = stack.pop()
            if current.children:
                stack.extend(current.children)
            else:
                total += len(current.skills)
        return total

    def collect_all_skills(self) -> list[Skill]:
        gathered: list[Skill] = []
        agenda = [self]
        while agenda:
            current = agenda.pop()
            if current.children:
                agenda.extend(current.children)
                continue
            if current.skills:
                gathered.extend(current.skills)
        return gathered

    def get_leaf_nodes(self) -> list["TreeNode"]:
        result: list[TreeNode] = []
        frontier = [self]
        while frontier:
            current = frontier.pop()
            if current.children:
                frontier.extend(current.children)
            else:
                result.append(current)
        return result

    def get_pending_split_nodes(self) -> list["TreeNode"]:
        flagged: list[TreeNode] = []
        queue = [self]
        while queue:
            current = queue.pop()
            if current.pending_split:
                flagged.append(current)
            queue.extend(current.children)
        return flagged

    def clear_pending_splits(self) -> None:
        for current in [self, *self._walk_descendants()]:
            current.pending_split = False

    def get_path(self) -> str:
        return self.id

    def to_dict(self) -> dict:
        payload: dict = {}
        payload.update(id=self.id, name=self.name, description=self.description)
        child_items = list(self.children)
        skill_items = list(self.skills)
        if child_items:
            serialized_children: list[dict] = []
            for child in child_items:
                serialized_children.append(child.to_dict())
            payload["children"] = serialized_children
        if skill_items:
            payload["skills"] = [item.to_dict() for item in skill_items]
        return payload

    def _walk_descendants(self):
        stack = list(self.children)
        while stack:
            current = stack.pop()
            yield current
            stack.extend(current.children)

    @classmethod
    def from_recursive_tree(
        cls,
        tree_dict: dict,
        depth: int = 0,
        parent_id: Optional[str] = None,
    ) -> "TreeNode":
        node = cls(
            node_id=tree_dict.get("id", "unknown"),
            name=tree_dict.get("name", ""),
            description=tree_dict.get("description", ""),
            depth=depth,
            parent_id=parent_id,
        )
        for child_payload in list(tree_dict.get("children", []) or []):
            node.children.append(cls.from_recursive_tree(child_payload, depth + 1, node.id))
        for skill_payload in list(tree_dict.get("skills", []) or []):
            node.skills.append(
                Skill(
                    skill_id=skill_payload.get("id", ""),
                    name=skill_payload.get("name", ""),
                    description=skill_payload.get("description", ""),
                    path=node.id,
                    skill_path=skill_payload.get("skill_path", ""),
                    content=skill_payload.get("content", ""),
                    github_url=skill_payload.get("github_url", ""),
                    stars=skill_payload.get("stars", 0),
                    is_official=skill_payload.get("is_official", False),
                    author=skill_payload.get("author", ""),
                )
            )
        return node

    @classmethod
    def from_capability_tree(cls, tree_dict: dict) -> "TreeNode":
        root = cls(node_id="root", name="Root", description="Skill Tree Root")
        domains = tree_dict.get("domains", {}) or {}
        for domain_id, domain_payload in domains.items():
            domain_node = cls(
                node_id=domain_id,
                name=domain_payload.get("name", domain_id),
                description=domain_payload.get("description", ""),
                depth=1,
                parent_id=root.id,
            )
            for type_id, type_payload in (domain_payload.get("types", {}) or {}).items():
                type_node = cls(
                    node_id=type_id,
                    name=type_payload.get("name", type_id),
                    description=type_payload.get("description", ""),
                    depth=2,
                    parent_id=domain_id,
                )
                for skill_payload in list(type_payload.get("skills", []) or []):
                    type_node.skills.append(
                        Skill(
                            skill_id=skill_payload.get("id", ""),
                            name=skill_payload.get("name", ""),
                            description=skill_payload.get("description", ""),
                            path="/".join([domain_id, type_id]),
                            github_url=skill_payload.get("github_url", ""),
                            stars=skill_payload.get("stars", 0),
                            is_official=skill_payload.get("is_official", False),
                            author=skill_payload.get("author", ""),
                        )
                    )
                domain_node.children.append(type_node)
            root.children.append(domain_node)
        return root


class SearchStep:
    def __init__(
        self,
        *,
        level: int,
        node_id: str,
        options: list[str],
        selected: list[str],
        is_parallel: bool = False,
    ) -> None:
        self.level = level
        self.node_id = node_id
        self.options = list(options)
        self.selected = list(selected)
        self.is_parallel = is_parallel


class MultiLevelSearchResult:
    def __init__(
        self,
        *,
        query: str,
        selected_skills: list[dict],
        steps: Optional[list[SearchStep]] = None,
        llm_calls: int = 0,
        parallel_rounds: int = 0,
        early_stops: int = 0,
    ) -> None:
        self.query = query
        self.selected_skills = list(selected_skills)
        self.steps = list(steps or [])
        self.llm_calls = llm_calls
        self.parallel_rounds = parallel_rounds
        self.early_stops = early_stops


def parse_json_from_response(response: str, default: Union[dict, list, None] = None) -> Union[dict, list]:
    fallback = {} if default is None else default
    if not isinstance(response, str):
        return fallback

    for candidate in _json_candidates(response):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, (dict, list)):
            return decoded
    return fallback


def _json_candidates(response: str) -> list[str]:
    raw = response.strip()
    candidates: list[str] = []
    if raw:
        candidates.append(raw)
    fenced = _strip_wrapping_fence(raw)
    if fenced and fenced != raw:
        candidates.insert(0, fenced)
    candidates.extend(_extract_balanced_fragments(response))
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _strip_wrapping_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    body = text.splitlines()
    if body and body[0].startswith("```"):
        body = body[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def _extract_balanced_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for opening, closing in (("{", "}"), ("[", "]")):
        index = text.find(opening)
        while index >= 0:
            fragment = _slice_balanced(text, index, opening, closing)
            if fragment:
                fragments.append(fragment)
                break
            index = text.find(opening, index + 1)
    return fragments


def _slice_balanced(text: str, start: int, opening: str, closing: str) -> Optional[str]:
    level = 0
    inside_string = False
    escaped = False
    for cursor, char in enumerate(text[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\" and inside_string:
            escaped = True
            continue
        if char == '"':
            inside_string = not inside_string
            continue
        if inside_string:
            continue
        if char == opening:
            level += 1
        elif char == closing:
            level -= 1
            if level == 0:
                return text[start:cursor + 1]
    return None
