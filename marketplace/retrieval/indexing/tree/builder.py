# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI
except ModuleNotFoundError:  # pragma: no cover
    APIConnectionError = APIError = APITimeoutError = AuthenticationError = None
    OpenAI = None

from shared.rich_compat import BarColumn, Console, Panel, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from indexing.scanners import create_scanner

from .schema import (
    DEFAULT_TREE_OUTPUT_PATH,
    DynamicTreeConfig,
    SKILL_DESCRIPTION_MAX_LENGTH,
    Skill,
    TreeManagerConfig,
    TreeNode,
)
from .expansion import TreeExpansionEngine as ExternalTreeExpansionEngine
from .grouping import TreeGroupingEngine
from .llm_runtime import TreeLLMRuntime as ExternalTreeLLMRuntime
from .prompts import (
    GROUP_MERGE_PROMPT,
    GROUP_DISCOVERY_PROMPT,
    SKILL_ASSIGNMENT_PROMPT,
    EQUIVALENCE_GROUPING_PROMPT,
)
from .preset_writer import TreePresetWriter as ExternalTreePresetWriter
from .repair import TreeRepairEngine as ExternalTreeRepairEngine
from .types import ChildGroup as ExternalChildGroup, QueuedNode as ExternalQueuedNode


console = Console()
_GENERIC_TERMS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "if", "in",
    "into", "is", "it", "of", "on", "or", "that", "the", "this", "to", "tool", "tools",
    "use", "used", "using", "when", "with",
}


_QueuedNode = ExternalQueuedNode
_ChildGroup = ExternalChildGroup
_TreeLLMRuntime = ExternalTreeLLMRuntime
_TreePresetWriter = ExternalTreePresetWriter
_TreeExpansionEngine = ExternalTreeExpansionEngine
_TreeRepairEngine = ExternalTreeRepairEngine


class TreeBuilder:
    """
    Unified tree builder with auto-selection and node splitting.

    Features:
    - Auto-selects build method based on skill count
    - Splits oversized nodes (> max_skills_per_node)
    - Simple tree visualization
    """

    # Token budget constants for auto batch size calculation
    PROMPT_OVERHEAD_TOKENS = 3000    # prompt template + instructions
    OUTPUT_RESERVE_TOKENS = 4000     # JSON response reserve
    AVG_TOKENS_PER_SKILL = 75       # average tokens per skill entry
    DEFAULT_CONTEXT_WINDOW = 128000  # fallback context window size
    DEFAULT_MAX_OUTPUT_TOKENS = 32768  # fallback max output tokens
    MAX_CONSECUTIVE_FAILURES = 5

    def __init__(
        self,
        skills_dir: Path | str | None = None,
        output_path: Path | str | None = None,
        config: Optional[DynamicTreeConfig] = None,
        manager_config: TreeManagerConfig | None = None,
        client: OpenAI | None = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        llm_seed: int | None = None,
        max_workers: Optional[int] = None,
        display_skills_dir: Path | str | None = None,
        item_type: str = "skill",
    ):
        mcfg = manager_config or TreeManagerConfig()
        build_cfg = mcfg.build
        if skills_dir is None:
            raise ValueError("TreeBuilder requires a non-empty skills_dir")
        self.scanner = create_scanner(item_type, skills_dir, display_items_dir=display_skills_dir)
        default_tree_path = DEFAULT_TREE_OUTPUT_PATH
        self.output_path = Path(output_path) if output_path else default_tree_path
        self.config = config or DynamicTreeConfig(
            branching_factor=mcfg.branching_factor,
            max_depth=mcfg.max_depth,
            root_categories=mcfg.root_categories,
        )
        self.model = str(model or "").strip()
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip()
        self.manager_config = mcfg
        self.llm_seed = llm_seed
        if not self.model:
            raise ValueError("TreeBuilder requires a non-empty llm model")
        if client is None and not self.api_key:
            raise ValueError("TreeBuilder requires a non-empty llm api key")
        self.client = (
            client
            if client is not None
            else (OpenAI(api_key=self.api_key, base_url=self.base_url) if OpenAI is not None else None)
        )
        self.max_workers = max_workers or build_cfg.max_workers
        self._postprocess_enabled = bool(build_cfg.postprocess_enabled)
        self.postprocess_max_passes = max(0, int(build_cfg.postprocess_max_passes))
        self.postprocess_min_skills = max(2, int(build_cfg.postprocess_min_skills))
        self._equiv_grouping_enabled = bool(build_cfg.equiv_grouping_enabled)
        self._equiv_max_groups_per_parent = max(2, int(build_cfg.equiv_max_groups_per_parent))
        self.equiv_allow_singleton_groups = bool(build_cfg.equiv_allow_singleton_groups)
        self._equiv_min_lexical_similarity = max(0.0, min(1.0, float(build_cfg.equiv_min_lexical_similarity)))
        self.deterministic_prompts = build_cfg.deterministic_prompts
        self.discovery_seed = build_cfg.discovery_seed
        self.prompt_fingerprint_version = build_cfg.prompt_fingerprint_version
        self.cache_observability = build_cfg.cache_observability

        self.llm_calls = 0
        self.retry_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_unknown = 0
        self.prompt_fingerprints: set[str] = set()
        self._leaf_skills = 0  # Skills that have reached leaf nodes
        self.counter_lock = threading.Lock()  # Protects llm_calls, _leaf_skills, consecutive_failures
        self.progress = None  # Rich progress bar
        self.progress_task = None
        self.batch_size_cache = None
        self.max_output_tokens_cache = None
        self.thread_local = threading.local()  # Per-thread truncation flag
        self.executor = None  # Shared executor, set in _build_tree
        self.llm_semaphore = threading.Semaphore(self.max_workers)  # Limit concurrent LLM calls
        self.consecutive_failures = 0
        self._llm_runtime = ExternalTreeLLMRuntime(self)
        self._preset_writer = ExternalTreePresetWriter(self)
        self._expansion_engine = ExternalTreeExpansionEngine(self)
        self._repair_engine = ExternalTreeRepairEngine(self)
        self._grouping_engine = TreeGroupingEngine(self)

    def auto_batch_size(self) -> int:
        """Calculate batch size from model context window."""
        return self._llm_runtime.auto_batch_size()

    def _get_max_output_tokens(self) -> int:
        """Get max output tokens for the model, with caching."""
        return self._llm_runtime.get_max_output_tokens()

    def _merged_extra_body(self) -> dict:
        return self._llm_runtime.merged_extra_body()

    def _model_limits(self) -> tuple[int, int]:
        """Resolve model limits."""
        return self._llm_runtime.model_limits()

    def build(self, verbose: bool = False, show_tree: bool = True, generate_html: bool = True) -> dict:
        console.print(Panel.fit("[bold cyan]Building Capability Tree[/bold cyan]", border_style="cyan"))
        skill_entries = self._load_skill_entries()
        if not skill_entries:
            console.print("[red]No skills found.[/red]")
            return {}

        tree_root = self._build_tree(skill_entries, verbose)
        tree_dict = self._tree_to_dict(tree_root)
        preset_dict = self._emit_tree_artifacts(
            tree_dict,
            show_tree=show_tree,
            generate_html=generate_html,
        )
        self._print_cache_stats()
        self._print_build_summary()
        return preset_dict

    def _load_skill_entries(self) -> list[dict]:
        console.print("\n[bold]Step 1: Scanning skills...[/bold]")
        skill_entries = self.scanner.to_dict_list()
        if skill_entries:
            console.print(f"[green]Found {len(skill_entries)} skills[/green]")
        return skill_entries

    def _emit_tree_artifacts(self, tree_dict: dict, *, show_tree: bool, generate_html: bool) -> dict:
        console.print("\n[bold]Step 3: Writing to file...[/bold]")
        preset_dict = self._tree_to_orchestrator_preset(tree_dict)
        self._write_yaml(preset_dict)
        if generate_html:
            from .visualizer import generate_html as gen_html

            html_path = self.output_path.with_suffix(".html")
            gen_html(tree_dict, html_path)
            console.print(f"[green]Generated HTML: {html_path}[/green]")
        if show_tree:
            console.print("\n[bold]Tree Structure:[/bold]")
            self._print_tree(tree_dict)
        return preset_dict

    def _print_build_summary(self) -> None:
        summary_lines = [f"[bold green]Done![/bold green] ({self.llm_calls} LLM calls)"]
        if self.cache_observability:
            summary_lines.extend(
                [
                    f"Cache hits/misses/unknown: {self.cache_hits}/{self.cache_misses}/{self.cache_unknown}",
                    f"Unique prompt fingerprints: {len(self.prompt_fingerprints)}",
                ]
            )
        summary_lines.append(f"Output: {self.output_path}")
        console.print(Panel.fit("\n".join(summary_lines), border_style="green"))

    def _build_tree(self, skills: list[dict], verbose: bool = False) -> TreeNode:
        console.print("\n[bold]Step 2: Building tree structure...[/bold]")
        root = TreeNode(node_id="root", name="Root", description="Skill capability tree root")
        self._leaf_skills = 0
        pending_nodes: deque[ExternalQueuedNode] = deque([ExternalQueuedNode(root, skills, 0, None)])
        active_jobs: dict = {}

        with self._tree_progress(total=len(skills)) as progress:
            self.progress = progress
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                self.executor = executor
                while pending_nodes or active_jobs:
                    self._submit_pending_nodes(pending_nodes, active_jobs, executor, verbose=verbose)
                    self._refresh_pending_metric(active_jobs)
                    if not active_jobs:
                        continue
                    self._harvest_finished_nodes(active_jobs, pending_nodes)

            self.progress = None
            self.executor = None

        self._repair_tree(root, source_skills=skills, verbose=verbose)
        return root

    def _tree_progress(self, *, total: int) -> Progress:
        status_text = (
            "("
            "{task.fields[leaf]}/{task.fields[total_count]} skills done, "
            "{task.fields[llm]} LLM, "
            "{task.fields[pending]} pending)"
        )
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn(status_text),
            console=console,
            transient=False,
        )
        self.progress_task = progress.add_task(
            "Building capability tree",
            total=total,
            pending=0,
            leaf=0,
            llm=0,
            total_count=total,
        )
        return progress

    def _submit_pending_nodes(
        self,
        pending_nodes: deque[ExternalQueuedNode],
        active_jobs: dict,
        executor: ThreadPoolExecutor,
        *,
        verbose: bool,
    ) -> None:
        while pending_nodes:
            job = pending_nodes.popleft()
            future = executor.submit(
                self._process_node,
                node=job.node,
                skills=job.skills,
                depth=job.depth,
                parent_context=job.parent_context,
                verbose=verbose,
            )
            active_jobs[future] = job

    def _refresh_pending_metric(self, active_jobs: dict) -> None:
        if self.progress is None or self.progress_task is None:
            return
        self.progress.update(self.progress_task, pending=len(active_jobs))

    def _harvest_finished_nodes(
        self,
        active_jobs: dict,
        pending_nodes: deque[ExternalQueuedNode],
    ) -> None:
        done, _ = wait(tuple(active_jobs.keys()), return_when=FIRST_COMPLETED)
        finished = sorted(done, key=lambda future: active_jobs[future].node.id)
        for future in finished:
            job = active_jobs.pop(future)
            try:
                child_groups = future.result()
            except Exception as exc:
                console.print(f"[red]Error processing {job.node.id}: {exc}[/red]")
                continue
            for child_group in child_groups:
                pending_nodes.append(self._queue_child_node(child_group, depth=job.depth + 1))

    def _repair_tree(self, root: TreeNode, *, source_skills: list[dict], verbose: bool) -> None:
        self._audit_tree(root, source_skills)
        if self._postprocess_enabled and self.postprocess_max_passes > 0:
            self._postprocess_tree(root, verbose)
        if self._equiv_grouping_enabled:
            self._normalize_to_equivalence_groups(root, verbose)
        self._audit_tree(root, source_skills)

    @staticmethod
    def _collect_leaf_skills(node: TreeNode) -> set:
        seen_ids: set[str] = set()
        frontier = [node]
        while frontier:
            current = frontier.pop()
            if current.children:
                frontier.extend(current.children)
                continue
            for skill in current.skills:
                seen_ids.add(skill.id)
        return seen_ids

    def _audit_tree(self, root: TreeNode, input_skills: list[dict]) -> None:
        expected_ids = {str(skill.get("id", "")).strip() for skill in input_skills}
        discovered_ids = self._collect_leaf_skills(root)
        missing_ids = sorted(skill_id for skill_id in expected_ids if skill_id and skill_id not in discovered_ids)
        if not missing_ids:
            return

        missing_lookup = {str(skill.get("id", "")).strip(): skill for skill in input_skills}
        recovered_payloads = [missing_lookup[skill_id] for skill_id in missing_ids if skill_id in missing_lookup]
        console.print(
            Panel(
                "\n".join(
                    [
                        f"[bold red]Recovered {len(recovered_payloads)} missing skills "
                        "after tree construction.[/bold red]",
                        "They have been attached under a fallback branch to preserve index completeness.",
                    ]
                ),
                title="[bold red]Tree Audit Recovery[/bold red]",
                border_style="red",
            )
        )

        fallback_branch = TreeNode(
            node_id="uncategorized",
            name="Uncategorized",
            description="Skills recovered by integrity audit after tree construction.",
            depth=1,
            parent_id=root.id,
        )
        self.assign_skills_to_leaf(fallback_branch, recovered_payloads)
        root.children.append(fallback_branch)

    @staticmethod
    def _queue_child_node(child_group: _ChildGroup, *, depth: int) -> _QueuedNode:
        """Convert a processed child subtree into a queued work item."""
        return _QueuedNode(
            node=child_group.node,
            skills=child_group.skills,
            depth=depth,
            parent_context={
                "name": child_group.node.name,
                "description": child_group.node.description,
            },
        )

    def _process_node(
        self,
        node: TreeNode,
        skills: list[dict],
        depth: int,
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> list[_ChildGroup]:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        return expansion_engine.process_node(
            node=node,
            skills=skills,
            depth=depth,
            parent_context=parent_context,
            verbose=verbose,
        )

    def classify_root_tags(
        self,
        skills: list[dict],
        verbose: bool = False,
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Classify skills into first-level root categories."""
        groups = self._root_group_definitions()
        assignments = self.classify_skills(skills, groups, verbose=verbose)
        # Use root-tag-specific recovery so build_skill_tags remains stable without
        # changing the generic tree-build recovery behavior.
        skill_ids = {str(item.get("id", "")).strip() for item in skills}
        assigned_ids = set(assignments.keys())
        missing = skill_ids - assigned_ids

        if missing and assignments:
            if len(missing) <= max(1, len(skills) // 2):
                retry_inputs = [
                    item for item in self.sorted_skills(skills) if str(item.get("id", "")).strip() in missing
                ]
                if retry_inputs:
                    retry_result = self.classify_skills_single(retry_inputs, groups, verbose=verbose, is_retry=True)
                    assignments.update(retry_result)
                    missing = skill_ids - set(assignments.keys())

            if missing:
                # Stable fallback: choose the highest-count group, and if tied keep
                # the first group in declared order to avoid lexical-bias drift.
                counts: dict[str, int] = {str(group_id): 0 for group_id in groups.keys()}
                for group_id in assignments.values():
                    gid = str(group_id)
                    if gid in counts:
                        counts[gid] += 1
                max_count = max(counts.values()) if counts else 0
                fallback_group_id = next(
                    (gid for gid in groups.keys() if counts.get(str(gid), 0) == max_count),
                    next(iter(groups.keys()), "uncategorized"),
                )
                for skill_id in sorted(missing):
                    assignments[skill_id] = str(fallback_group_id)
                if verbose:
                    console.print(
                        Panel(
                            f"[bold red]{len(missing)}/{len(skills)} skills were placed into fallback group "
                            f"'{fallback_group_id}'.[/bold red]",
                            title="[bold red]Root Tag Recovery[/bold red]",
                            border_style="red",
                        )
                    )
        elif missing and not assignments:
            # Keep behavior aligned with prior flow: if nothing is assigned, return
            # empty mapping and let caller-side fallback handle it.
            assignments = {}

        normalized_groups = {
            str(group_id): {
                "name": str(payload.get("name") or group_id),
                "description": str(payload.get("description") or ""),
            }
            for group_id, payload in groups.items()
        }
        return assignments, normalized_groups

    @staticmethod
    def classify_root_tags_with_llm(
        *,
        skills: list[dict],
        manager_config: TreeManagerConfig,
        model: str,
        api_key: str = "",
        base_url: str = "",
        client: OpenAI | None = None,
        llm_seed: int | None = None,
        max_workers: int | None = None,
        root_categories: dict | None = None,
        skills_dir: Path | str = ".",
        output_path: Path | str = "_root_tag_only.yaml",
        item_type: str = "skill",
        verbose: bool = False,
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """
        LLM-only helper for first-level classification.
        Does not build/write the full tree.
        """
        effective_seed = llm_seed
        if effective_seed is None:
            effective_seed = int(getattr(manager_config.build, "discovery_seed", 42))

        builder = TreeBuilder(
            skills_dir=skills_dir,
            output_path=output_path,
            config=DynamicTreeConfig(
                branching_factor=manager_config.branching_factor,
                max_depth=manager_config.max_depth,
                root_categories=root_categories,
            ),
            manager_config=manager_config,
            client=client,
            model=model,
            api_key=api_key,
            base_url=base_url,
            llm_seed=effective_seed,
            max_workers=max_workers,
            item_type=item_type,
        )
        return builder.classify_root_tags(skills, verbose=verbose)

    # =========================================================================
    # Two-phase classification: discover groups -> flat assignment
    # =========================================================================

    def assign_skills_to_leaf(self, node: TreeNode, skills: list[dict]) -> None:
        """Assign skill dicts to a leaf node as Skill objects. Updates progress counter."""
        for skill_data in skills:
            node.skills.append(self._skill_from_data(skill_data, path=node.id))
        # Update leaf skills count (thread-safe)
        with self.counter_lock:
            self._leaf_skills += len(skills)
            if self.progress and self.progress_task is not None:
                self.progress.update(self.progress_task, leaf=self._leaf_skills, completed=self._leaf_skills)

    @staticmethod
    def _skill_from_data(skill_data: dict, *, path: str) -> Skill:
        """Materialize a Skill object from a skill dict."""
        return Skill(
            skill_id=skill_data["id"],
            name=skill_data.get("name", skill_data["id"]),
            description=skill_data.get("description", ""),
            path=path,
            skill_path=skill_data.get("skill_path", ""),
            content=skill_data.get("content", ""),
            github_url=skill_data.get("github_url", ""),
            stars=skill_data.get("stars", 0),
            is_official=skill_data.get("is_official", False),
            author=skill_data.get("author", ""),
        )

    @staticmethod
    def skill_to_data(skill: Skill) -> dict:
        """Convert a Skill object back into a mutable skill dict."""
        return skill.to_dict(include_content=True)

    def _root_group_definitions(self) -> dict[str, dict[str, str]]:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        return expansion_engine.root_group_definitions()

    def _create_child_node(
        self,
        *,
        parent: TreeNode,
        group_id: str,
        group_data: dict,
        depth: int,
    ) -> TreeNode:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        return expansion_engine.create_child_node(parent=parent, group_id=group_id, group_data=group_data, depth=depth)

    def _build_children_from_groups(
        self,
        node: TreeNode,
        skills: list[dict],
        groups: dict,
        depth: int,
        verbose: bool = False,
    ) -> list[_ChildGroup]:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        return expansion_engine.build_children_from_groups(node, skills, groups, depth, verbose)

    def _reassign_skills_to_children(
        self,
        unassigned_skills: list[dict],
        children_to_process: list[_ChildGroup],
    ) -> tuple[int, list[dict]]:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        return expansion_engine.reassign_skills_to_children(unassigned_skills, children_to_process)

    def _assign_unassigned_skills(
        self,
        *,
        node: TreeNode,
        all_skills: list[dict],
        remaining_skill_map: dict[str, dict],
        children_to_process: list[_ChildGroup],
        verbose: bool = False,
    ) -> None:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        expansion_engine.assign_unassigned_skills(
            node=node,
            all_skills=all_skills,
            remaining_skill_map=remaining_skill_map,
            children_to_process=children_to_process,
            verbose=verbose,
        )

    def rewrite_node_label_after_singleton(
        self,
        node: TreeNode,
        children_to_process: list[_ChildGroup],
        verbose: bool = False,
    ) -> None:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        expansion_engine.rewrite_node_label_after_singleton(node, children_to_process, verbose)

    def _postprocess_tree(self, root: TreeNode, verbose: bool = False) -> None:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        repair_engine.postprocess_tree(root, verbose)

    def _postprocess_node(self, node: TreeNode, verbose: bool = False) -> int:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        return repair_engine.postprocess_node(node, verbose)

    def _rebalance_child_assignments(self, node: TreeNode, verbose: bool = False) -> int:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        return repair_engine.rebalance_child_assignments(node, verbose)

    def _collect_subtree_skill_locations(self, node: TreeNode) -> list[tuple[TreeNode, dict]]:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        return repair_engine.collect_subtree_skill_locations(node)

    def collect_subtree_skill_dicts(self, node: TreeNode) -> list[dict]:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        return repair_engine.collect_subtree_skill_dicts(node)

    def existing_child_groups(self, children: list[TreeNode]) -> list[_ChildGroup]:
        expansion_engine = getattr(self, "_expansion_engine", None) or _TreeExpansionEngine(self)
        return expansion_engine.existing_child_groups(children)

    def _choose_child_for_skill(self, skill_data: dict, children: list[TreeNode]) -> TreeNode:
        """Choose the best direct child for a skill, falling back to the largest subtree."""
        child_by_id = {child.id: child for child in children}
        groups = {
            child.id: {
                "name": child.name,
                "description": child.description,
            }
            for child in children
        }
        assignment = self.classify_skills_single(
            [skill_data],
            groups,
            verbose=False,
            is_retry=True,
        )
        child_id = assignment.get(str(skill_data.get("id", "")).strip())
        if child_id in child_by_id:
            return child_by_id[child_id]
        return max(children, key=lambda child: child.count_all_skills())

    def insert_skill_into_subtree(self, node: TreeNode, skill_data: dict) -> None:
        """Insert a skill into the best-fitting location inside an existing subtree."""
        skill_id = str(skill_data.get("id", "")).strip()
        if not skill_id:
            return

        if node.is_leaf or not node.children:
            if any(skill.id == skill_id for skill in node.skills):
                return
            node.skills.append(self._skill_from_data(skill_data, path=node.id))
            return

        target_child = self._choose_child_for_skill(skill_data, node.children)
        self.insert_skill_into_subtree(target_child, skill_data)

    def prune_empty_children(self, node: TreeNode) -> int:
        """Remove empty child subtrees after skill moves."""
        removed = 0
        kept_children: list[TreeNode] = []
        for child in node.children:
            removed += self.prune_empty_children(child)
            if child.children:
                if child.count_all_skills() <= 0:
                    removed += 1
                    continue
            elif not child.skills:
                removed += 1
                continue
            kept_children.append(child)
        node.children = kept_children
        return removed

    def repair_small_leaf_children(self, node: TreeNode) -> int:
        """
        Merge direct leaf children with <2 skills back into their siblings.
        This keeps post-process from leaving obviously unstable tiny groups behind.
        """
        if self.equiv_allow_singleton_groups:
            return 0
        if len(node.children) < 2:
            return 0

        tiny_leaf_children = [
            child
            for child in node.children
            if child.is_leaf and 0 < len(child.skills) < 2
        ]
        if not tiny_leaf_children:
            return 0

        remaining_children = [child for child in node.children if child not in tiny_leaf_children]
        if len(remaining_children) < 2:
            return 0

        reassigned_skills = [self.skill_to_data(skill) for child in tiny_leaf_children for skill in child.skills]
        node.children = remaining_children
        for skill_data in reassigned_skills:
            target_child = self._choose_child_for_skill(skill_data, node.children)
            self.insert_skill_into_subtree(target_child, skill_data)
        return len(reassigned_skills)

    def _normalize_to_equivalence_groups(self, root: TreeNode, verbose: bool = False) -> None:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        repair_engine.normalize_to_equivalence_groups(root, verbose)

    @staticmethod
    def _is_second_leaf_node(node: TreeNode) -> bool:
        """Second-leaf node: has children and all children are leaf nodes."""
        return _TreeRepairEngine.is_second_leaf_node(node)

    def _split_second_leaf_node_into_equiv_groups(
        self,
        parent_node: TreeNode,
        second_leaf_node: TreeNode,
        verbose: bool = False,
    ) -> list[TreeNode]:
        repair_engine = getattr(self, "_repair_engine", None) or _TreeRepairEngine(self)
        return repair_engine.split_second_leaf_node_into_equiv_groups(parent_node, second_leaf_node, verbose)

    def discover_equivalence_groups(
        self,
        second_leaf_node: TreeNode,
        leaf_children: list[TreeNode],
        verbose: bool = False,
    ) -> dict:
        """Ask LLM to partition second-leaf children into equivalence groups."""
        leaf_lines = []
        for leaf in leaf_children:
            sample_skill_ids = ", ".join(skill.id for skill in leaf.skills[:5]) or "(none)"
            leaf_lines.append(
                f"- id: {leaf.id}\n"
                f"  name: {leaf.name}\n"
                f"  description: {leaf.description or '(no description)'}\n"
                f"  sample_skill_ids: {sample_skill_ids}"
            )

        prompt = EQUIVALENCE_GROUPING_PROMPT.format(
            parent_id=second_leaf_node.id,
            parent_name=second_leaf_node.name,
            parent_description=second_leaf_node.description or "(no description)",
            leaf_nodes="\n".join(leaf_lines),
            max_groups=self._equiv_max_groups_per_parent,
        )
        result = self.call_llm_json(prompt)
        groups = result.get("groups", {})
        if not isinstance(groups, dict):
            if verbose:
                console.print(f"[yellow]  Equivalence grouping failed for '{second_leaf_node.id}'[/yellow]")
            return {}
        return groups

    def normalize_equivalence_groups(self, leaf_children: list[TreeNode], groups: dict) -> list[dict]:
        """
        Normalize and repair LLM equivalence groups.

        Guarantees:
        - Every original leaf appears in exactly one output group
        - Unknown leaf IDs are ignored
        - Empty groups are removed
        """
        leaf_map = {leaf.id: leaf for leaf in leaf_children}
        assigned: set[str] = set()
        normalized: list[dict] = []

        for group_id, group_data in self._iter_group_items(groups):
            if not isinstance(group_data, dict):
                continue
            raw_leaf_ids = group_data.get("leaf_ids", [])
            if not isinstance(raw_leaf_ids, list):
                raw_leaf_ids = []
            leaf_nodes = []
            for leaf_id in raw_leaf_ids:
                lid = str(leaf_id).strip()
                if not lid or lid in assigned:
                    continue
                leaf = leaf_map.get(lid)
                if leaf is None:
                    continue
                assigned.add(lid)
                leaf_nodes.append(leaf)
            if not leaf_nodes:
                continue
            normalized.append(
                {
                    "id": self.build_equivalence_group_id(
                        group_id=str(group_id).strip(),
                        group_name=str(group_data.get("name") or "").strip(),
                        fallback="equiv-group",
                    ),
                    "name": str(group_data.get("name") or group_id),
                    "description": str(group_data.get("description") or ""),
                    "leaf_nodes": leaf_nodes,
                }
            )

        # Recovery: assign missing leaves conservatively.
        missing = [leaf for leaf in leaf_children if leaf.id not in assigned]
        if missing and normalized and not self.equiv_allow_singleton_groups:
            largest_idx = max(range(len(normalized)), key=lambda idx: len(normalized[idx]["leaf_nodes"]))
            normalized[largest_idx]["leaf_nodes"].extend(missing)
        elif missing:
            for leaf in missing:
                normalized.append(
                    {
                        "id": f"equiv-{self._slug_term(leaf.id, fallback='leaf')}",
                        "name": leaf.name or leaf.id,
                        "description": leaf.description or "Equivalent capability group.",
                        "leaf_nodes": [leaf],
                    }
                )

        normalized = self._split_equivalence_groups_by_similarity(normalized)

        # Keep deterministic order.
        if self.deterministic_prompts:
            for item in normalized:
                item["leaf_nodes"] = sorted(item["leaf_nodes"], key=lambda leaf: leaf.id)
            normalized.sort(key=lambda item: str(item.get("id", "")))
        return normalized

    def build_equivalence_group_id(self, *, group_id: str, group_name: str, fallback: str) -> str:
        """
        Build a stable, readable node id for equivalence groups.

        LLMs often emit placeholder ids like G1/G2. We prefer semantic ids derived
        from the group name and only fall back to the raw id when it is informative.
        """
        raw_name = str(group_name or "").strip()
        raw_id = str(group_id or "").strip()
        generic_id = bool(re.fullmatch(r"g\d+(?:-\d+)?", raw_id.lower()))

        if raw_name:
            return self._slug_term(raw_name, fallback=fallback)
        if raw_id and not generic_id:
            return self._slug_term(raw_id, fallback=fallback)
        return self._slug_term(fallback, fallback="equiv-group")

    def _split_equivalence_groups_by_similarity(self, groups: list[dict]) -> list[dict]:
        """
        Split coarse LLM groups by lexical similarity connectivity.

        This is a conservative guardrail: if a group has disconnected semantic components
        under the configured similarity threshold, we keep them as separate equivalence groups.
        """
        if not groups:
            return groups
        if self._equiv_min_lexical_similarity <= 0:
            return groups

        refined: list[dict] = []
        for group in groups:
            leaf_nodes = list(group.get("leaf_nodes", []) or [])
            if len(leaf_nodes) <= 1:
                refined.append(group)
                continue
            components = self._connected_leaf_components(leaf_nodes, self._equiv_min_lexical_similarity)
            if len(components) <= 1:
                refined.append(group)
                continue
            for idx, component in enumerate(components, start=1):
                refined.append(
                    {
                        "id": f"{group.get('id', 'equiv-group')}-{idx}",
                        "name": str(group.get("name", "Equivalent Group")),
                        "description": str(group.get("description", "")),
                        "leaf_nodes": component,
                    }
                )
        return refined

    @staticmethod
    def _connected_leaf_components(
        leaf_nodes: list[TreeNode],
        threshold: float,
    ) -> list[list[TreeNode]]:
        """Connected components over pairwise lexical similarity graph."""
        if len(leaf_nodes) <= 1:
            return [leaf_nodes]

        def tokens(leaf: TreeNode) -> set[str]:
            text = f"{leaf.id} {leaf.name} {leaf.description}"
            words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
            return {word for word in words if word not in _GENERIC_TERMS}

        token_map = {leaf.id: tokens(leaf) for leaf in leaf_nodes}
        index = {leaf.id: leaf for leaf in leaf_nodes}
        adj: dict[str, set[str]] = {leaf.id: set() for leaf in leaf_nodes}

        ids = [leaf.id for leaf in leaf_nodes]
        for i, left_id in enumerate(ids):
            left_tokens = token_map[left_id]
            for right_id in ids[i + 1:]:
                right_tokens = token_map[right_id]
                union = left_tokens | right_tokens
                sim = (len(left_tokens & right_tokens) / len(union)) if union else 1.0
                if sim >= threshold:
                    adj[left_id].add(right_id)
                    adj[right_id].add(left_id)

        components: list[list[TreeNode]] = []
        visited: set[str] = set()
        for node_id in ids:
            if node_id in visited:
                continue
            stack = [node_id]
            visited.add(node_id)
            comp_ids: list[str] = []
            while stack:
                cur = stack.pop()
                comp_ids.append(cur)
                for nxt in adj[cur]:
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    stack.append(nxt)
            components.append([index[item_id] for item_id in comp_ids])
        return components

    def sorted_skills(self, skills: list[dict]) -> list[dict]:
        return self._grouping_engine.sorted_skills(skills)

    def _iter_group_items(self, groups: dict):
        return self._grouping_engine.iter_group_items(groups)

    def _normalize_prompt_for_fingerprint(self, prompt: str) -> str:
        """Normalize prompt text to keep fingerprint stable across runs."""
        return self._llm_runtime.normalize_prompt_for_fingerprint(prompt)

    def _prompt_fingerprint(self, prompt: str) -> str:
        """Compute deterministic prompt fingerprint."""
        return self._llm_runtime.prompt_fingerprint(prompt)

    def _sampling_seed(self, parent_context: Optional[dict], skills_count: int) -> int:
        return self._grouping_engine.sampling_seed(parent_context, skills_count)

    def _extract_cache_hit(self, response) -> Optional[bool]:
        """Best-effort extraction of cache hit status from response metadata."""
        return self._llm_runtime.extract_cache_hit(response)

    def _extract_cache_hit_from_mapping(self, mapping: dict) -> Optional[bool]:
        """Parse cache hit from a mapping (recursively over nested dicts)."""
        return self._llm_runtime.extract_cache_hit_from_mapping(mapping)

    def _record_cache_observation(self, cache_hit: Optional[bool]) -> None:
        """Aggregate cache hit/miss counters."""
        self._llm_runtime.record_cache_observation(cache_hit)

    def _print_cache_stats(self) -> None:
        """Print cache observability metrics for intuitive build feedback."""
        self._llm_runtime.print_cache_stats()

    def build_groups_from_assignments(self, groups: dict, assignments: dict) -> dict:
        return self._grouping_engine.build_groups_from_assignments(groups, assignments)

    def classify_skills(self, skills: list[dict], groups: dict, verbose: bool = False) -> dict:
        return self._grouping_engine.classify_skills(skills, groups, verbose=verbose)

    def classify_skills_single(
        self,
        skills: list[dict],
        groups: dict,
        verbose: bool = False,
        is_retry: bool = False,
    ) -> dict:
        return self._grouping_engine.classify_skills_single(
            skills,
            groups,
            verbose=verbose,
            is_retry=is_retry,
        )

    def _batched_classify_skills(
        self,
        skills: list[dict],
        groups: dict,
        batch_size: int,
        verbose: bool = False,
    ) -> dict:
        return self._grouping_engine.batched_classify_skills(
            skills,
            groups,
            batch_size=batch_size,
            verbose=verbose,
        )

    def validate_and_recover(
        self,
        skills: list[dict],
        groups: dict,
        assignments: dict,
        verbose: bool = False,
    ) -> dict:
        return self._grouping_engine.validate_and_recover(
            skills,
            groups,
            assignments,
            verbose=verbose,
        )

    def _discover_groups(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        return self._grouping_engine.discover_groups(skills, parent_context, verbose=verbose)

    def _merge_group_definitions(self, all_group_defs: list[dict], verbose: bool = False) -> dict:
        return self._grouping_engine.merge_group_definitions(all_group_defs, verbose=verbose)

    def split_skills(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        return self._grouping_engine.split_skills(skills, parent_context, verbose=verbose)

    def _split_skills_single(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        return self._grouping_engine.split_skills_single(skills, parent_context, verbose=verbose)

    def _batched_split_skills(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        batch_size: int,
        verbose: bool = False,
    ) -> dict:
        return self._grouping_engine.batched_split_skills(
            skills,
            parent_context,
            batch_size=batch_size,
            verbose=verbose,
        )

    def _call_llm(self, prompt: str, is_retry: bool = False, retry_left: int | None = None) -> str:
        """Call LLM and return response."""
        return self._llm_runtime.call_llm(prompt, is_retry=is_retry, retry_left=retry_left)

    def call_llm_json(self, prompt: str, max_retries: int = 3, is_retry: bool = False) -> dict:
        """Call LLM expecting a JSON dict response, with retry on format errors."""
        return self._llm_runtime.call_llm_json(prompt, max_retries=max_retries, is_retry=is_retry)

    def _format_skills_list(self, skills: list[dict]) -> str:
        return self._grouping_engine.format_skills_list(skills)

    def _tree_to_dict(self, tree: TreeNode) -> dict:
        writer = getattr(self, "_preset_writer", None)
        if writer is None:
            writer = _TreePresetWriter(self)
        converted = writer.tree_to_dict(tree)
        return dict(converted)

    def _tree_to_orchestrator_preset(self, tree_dict: dict) -> dict:
        return self._preset_writer.tree_to_orchestrator_preset(tree_dict)

    def _flatten_capability_tree(self, tree: dict) -> list[dict]:
        return self._preset_writer.flatten_capability_tree(tree)

    def _rename_leaf_nodes(self, nodes: list[dict]) -> list[dict]:
        return self._preset_writer.rename_leaf_nodes(nodes)

    def _compact_leaf_cid_seed(self, *, worker_id: str, display_name: str, old_term: str) -> str:
        preset_writer = getattr(self, "_preset_writer", None)
        if preset_writer is None:
            preset_writer = _TreePresetWriter(self)
        return preset_writer.compact_leaf_cid_seed(
            worker_id=worker_id,
            display_name=display_name,
            old_term=old_term,
        )

    def _cid_term(self, value: str, fallback: str = "Node") -> str:
        preset_writer = getattr(self, "_preset_writer", None)
        if preset_writer is None:
            preset_writer = _TreePresetWriter(self)
        return preset_writer.cid_term(value, fallback=fallback)

    @staticmethod
    def _build_routing_policy(nodes: list[dict]) -> str:
        root_entries = sorted(
            [item for item in nodes if "." not in str(item.get("cid", ""))],
            key=lambda item: str(item.get("cid", "")),
        )
        lines = [
            "Route by descending the node tree one level at a time.",
            "Treat a user request as potentially multi-step unless the latest observation "
            "already fully satisfies every explicit requirement.",
            "Prefer leaves whose descriptions best match the next unmet sub-problem in the user request.",
            "After a worker returns, check whether unmet requirements still remain; if they "
            "do, continue routing instead of finishing early.",
            "Do not jump to User.Final after a single worker call when the user asked for "
            "multiple actions, dependencies, or deliverables.",
            "Use worker observations as intermediate state: one skill may gather facts or "
            "create prerequisites for a later skill.",
            "When multiple branches overlap, use the child descriptions as the local decision surface.",
            "Choose User.Final only when the latest observation set is sufficient to answer "
            "the whole user request, not just one subtask.",
        ]
        for item in root_entries:
            lines.append(f"If the request matches '{item['cid']}', continue under that branch.")
        return "\n".join(f"- {line}" for line in lines)

    def _build_tree_sketch(self, nodes: list[dict]) -> str:
        return self._preset_writer.build_tree_sketch(nodes)

    def _slug_term(self, value: str, fallback: str = "node") -> str:
        preset_writer = getattr(self, "_preset_writer", None)
        if preset_writer is None:
            preset_writer = _TreePresetWriter(self)
        return preset_writer.slug_term(value, fallback=fallback)

    @staticmethod
    def _join_cid(parent: str, child: str) -> str:
        return _TreePresetWriter.join_cid(parent, child)

    @staticmethod
    def _parent_cid(cid: str) -> str:
        return _TreePresetWriter.parent_cid(cid)

    def _unique_child_cid(self, parent_cid: str, segment: str, used: set[str]) -> str:
        return self._preset_writer.unique_child_cid(parent_cid, segment, used)

    def _extract_keywords(self, *values: str, limit: int = 8) -> list[str]:
        return self._preset_writer.extract_keywords(*values, limit=limit)

    def _node_to_dict(self, node: TreeNode) -> dict:
        writer = getattr(self, "_preset_writer", None)
        if writer is None:
            writer = _TreePresetWriter(self)
        payload = writer.node_to_dict(node)
        return payload.copy()

    def _write_yaml(self, tree_dict: dict) -> None:
        """Write tree to YAML file."""
        self._preset_writer.write_yaml(tree_dict)

    def _print_tree(self, tree_dict: dict) -> None:
        """Print tree structure using rich (supports arbitrary depth)."""
        self._preset_writer.print_tree(tree_dict)

    def _add_node_to_rich_tree(self, parent_branch, node_dict: dict) -> None:
        """Recursively add nodes to rich tree."""
        self._preset_writer.add_node_to_rich_tree(parent_branch, node_dict)

    def _count_skills_in_dict(self, node_dict: dict) -> int:
        """Recursively count skills in a node dict."""
        return self._preset_writer.count_skills_in_dict(node_dict)


# Convenience function
@dataclass
class BuildTreeRequest:
    skills_dir: Path | str | None = None
    output_path: Path | str | None = None
    config: DynamicTreeConfig | None = None
    manager_config: TreeManagerConfig | None = None
    client: OpenAI | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    llm_seed: int | None = None
    max_workers: int | None = None
    verbose: bool = False
    show_tree: bool = True
    generate_html: bool = True
    display_skills_dir: Path | str | None = None
    item_type: str = "skill"


def build_tree(request: BuildTreeRequest) -> dict:
    """Build capability tree."""
    builder = TreeBuilder(
        skills_dir=request.skills_dir,
        output_path=request.output_path,
        config=request.config,
        manager_config=request.manager_config,
        client=request.client,
        model=request.model,
        api_key=request.api_key,
        base_url=request.base_url,
        llm_seed=request.llm_seed,
        max_workers=request.max_workers,
        display_skills_dir=request.display_skills_dir,
        item_type=request.item_type,
    )
    return builder.build(
        verbose=request.verbose,
        show_tree=request.show_tree,
        generate_html=request.generate_html,
    )
