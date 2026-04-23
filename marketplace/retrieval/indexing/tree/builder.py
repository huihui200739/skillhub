from __future__ import annotations

"""
Tree Builder - Build capability tree from skills using LLM classification.

Two-phase build process:
1. Structure Discovery: Sample skills to discover domain/type structure
2. Anchored Classification: Classify remaining skills into discovered structure
"""

import hashlib
import json
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, as_completed
from pathlib import Path
from typing import Optional

try:
    from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI
except ModuleNotFoundError:  # pragma: no cover
    APIConnectionError = APIError = APITimeoutError = AuthenticationError = None
    OpenAI = None

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

from shared.rich_compat import (
    BarColumn,
    Console,
    Panel,
    Progress,
    RichTree,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from indexing.scanners import create_scanner

from .prompts import (
    EQUIVALENCE_GROUPING_PROMPT,
    GROUP_DISCOVERY_PROMPT,
    GROUP_MERGE_PROMPT,
    NODE_LABEL_REWRITE_PROMPT,
    SKILL_ASSIGNMENT_PROMPT,
)
from .schema import (
    DEFAULT_TREE_OUTPUT_PATH,
    DynamicTreeConfig,
    FIXED_ROOT_CATEGORIES,
    SKILL_DESCRIPTION_MAX_LENGTH,
    Skill,
    TreeManagerConfig,
    TreeNode,
    parse_json_from_response,
)

# litellm._turn_on_debug()

console = Console()
_GENERIC_TERMS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "if", "in",
    "into", "is", "it", "of", "on", "or", "that", "the", "this", "to", "tool", "tools",
    "use", "used", "using", "when", "with",
}


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
        self._manager_config = mcfg
        self._llm_seed = llm_seed
        if not self.model:
            raise ValueError("TreeBuilder requires a non-empty llm model")
        if client is None and not self.api_key:
            raise ValueError("TreeBuilder requires a non-empty llm api key")
        self._client = client if client is not None else (
            OpenAI(api_key=self.api_key, base_url=self.base_url) if OpenAI is not None else None)
        self.max_workers = max_workers or build_cfg.max_workers
        self._postprocess_enabled = bool(build_cfg.postprocess_enabled)
        self._postprocess_max_passes = max(0, int(build_cfg.postprocess_max_passes))
        self._postprocess_min_skills = max(2, int(build_cfg.postprocess_min_skills))
        self._equiv_grouping_enabled = bool(build_cfg.equiv_grouping_enabled)
        self._equiv_max_groups_per_parent = max(2, int(build_cfg.equiv_max_groups_per_parent))
        self._equiv_allow_singleton_groups = bool(build_cfg.equiv_allow_singleton_groups)
        self._equiv_min_lexical_similarity = max(0.0, min(1.0, float(build_cfg.equiv_min_lexical_similarity)))
        self._deterministic_prompts = build_cfg.deterministic_prompts
        self._discovery_seed = build_cfg.discovery_seed
        self._prompt_fingerprint_version = build_cfg.prompt_fingerprint_version
        self._cache_observability = build_cfg.cache_observability

        self._llm_calls = 0
        self._retry_calls = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_unknown = 0
        self._prompt_fingerprints: set[str] = set()
        self._leaf_skills = 0  # Skills that have reached leaf nodes
        self._counter_lock = threading.Lock()  # Protects _llm_calls, _leaf_skills, _consecutive_failures
        self._progress = None  # Rich progress bar
        self._progress_task = None
        self._batch_size_cache = None
        self._max_output_tokens_cache = None
        self._thread_local = threading.local()  # Per-thread truncation flag
        self._executor = None  # Shared executor, set in _build_tree
        self._llm_semaphore = threading.Semaphore(self.max_workers)  # Limit concurrent LLM calls
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5

    def _auto_batch_size(self) -> int:
        """Calculate batch size from model context window."""
        if self._batch_size_cache is not None:
            return self._batch_size_cache
        ctx_window, _ = self._model_limits()
        available = ctx_window - self.PROMPT_OVERHEAD_TOKENS - self.OUTPUT_RESERVE_TOKENS
        batch_size = available // self.AVG_TOKENS_PER_SKILL
        self._batch_size_cache = max(50, min(batch_size, 1000))
        return self._batch_size_cache

    def _get_max_output_tokens(self) -> int:
        """Get max output tokens for the model, with caching."""
        if self._max_output_tokens_cache is not None:
            return self._max_output_tokens_cache
        _, max_out = self._model_limits()
        max_output_override = int(getattr(self._manager_config.build, "max_output_tokens", 0) or 0)
        if max_output_override > 0:
            self._max_output_tokens_cache = max_output_override
        else:
            self._max_output_tokens_cache = min(int(max_out), 4096)
        return self._max_output_tokens_cache

    def _merged_extra_body(self) -> dict:
        merged = {
            "thinking": {"type": "disabled"},
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0.0,
            "top_p": 1.0,
        }
        if self._llm_seed is not None:
            seed_text = str(self._llm_seed).strip()
            if seed_text.lstrip("-").isdigit():
                merged["seed"] = int(seed_text)
        return merged

    def _model_limits(self) -> tuple[int, int]:
        """
        Resolve model limits without litellm.
        Priority:
        1) explicit tree-config overrides
        2) name heuristics
        3) defaults
        """
        ctx_cfg = int(getattr(self._manager_config.build, "context_window", 0) or 0)
        out_cfg = int(getattr(self._manager_config.build, "max_output_tokens", 0) or 0)
        if ctx_cfg > 0 and out_cfg > 0:
            return ctx_cfg, out_cfg

        model_name = (self.model or "").lower()
        large_window_model = any(
            marker in model_name
            for marker in ("gpt-4.1", "gpt-4o", "claude", "doubao")
        )
        if large_window_model:
            return 128000, 32768
        if "gpt-5" in model_name:
            return 200000, 65536
        return self.DEFAULT_CONTEXT_WINDOW, self.DEFAULT_MAX_OUTPUT_TOKENS

    def build(self, verbose: bool = False, show_tree: bool = True, generate_html: bool = True) -> dict:
        """
        Build capability tree from skill_seeds.

        Args:
            verbose: Print detailed progress
            show_tree: Display tree after building
            generate_html: Generate HTML visualization

        Returns:
            Tree dict structure
        """
        console.print(Panel.fit(
            "[bold cyan]Building Capability Tree[/bold cyan]",
            border_style="cyan",
        ))

        # Step 1: Scan skills
        console.print("\n[bold]Step 1: Scanning skills...[/bold]")
        skills = self.scanner.to_dict_list()

        if not skills:
            console.print("[red]No skills found.[/red]")
            return {}

        console.print(f"[green]Found {len(skills)} skills[/green]")

        # Step 2: Build tree with auto-selection
        console.print("\n[bold]Step 2: Building tree structure...[/bold]")
        tree = self._build_tree(skills, verbose)

        # Step 3: Convert to dict and write
        console.print("\n[bold]Step 3: Writing to file...[/bold]")
        tree_dict = self._tree_to_dict(tree)
        preset_dict = self._tree_to_orchestrator_preset(tree_dict)
        self._write_yaml(preset_dict)

        # Step 4: Generate HTML visualization
        if generate_html:
            from .visualizer import generate_html as gen_html
            html_path = self.output_path.with_suffix('.html')
            gen_html(tree_dict, html_path)
            console.print(f"[green]Generated HTML: {html_path}[/green]")

        # Show tree
        if show_tree:
            console.print("\n[bold]Tree Structure:[/bold]")
            self._print_tree(tree_dict)

        self._print_cache_stats()

        done_lines = [f"[bold green]Done![/bold green] ({self._llm_calls} LLM calls)"]
        if self._cache_observability:
            done_lines.append(
                f"Cache hits/misses/unknown: {self._cache_hits}/{self._cache_misses}/{self._cache_unknown}"
            )
            done_lines.append(f"Unique prompt fingerprints: {len(self._prompt_fingerprints)}")
        done_lines.append(f"Output: {self.output_path}")

        console.print(Panel.fit(
            "\n".join(done_lines),
            border_style="green",
        ))

        return preset_dict

    def _build_tree(self, skills: list[dict], verbose: bool = False) -> TreeNode:
        """Build tree using queue-based parallel approach (no recursion)."""
        root = TreeNode(id="root", name="Root", description="Skill capability tree root")

        # Queue: (node, skills, depth, parent_context)
        queue = [(root, skills, 0, None)]
        futures_map = {}  # future -> (node, depth)

        total_skills = len(skills)
        self._leaf_skills = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn(
                "({task.fields[leaf]}/{task.fields[total_count]} skills done, "
                "{task.fields[llm]} LLM, {task.fields[pending]} pending)"
            ),
            console=console,
            transient=False,
        ) as progress:
            self._progress = progress
            self._progress_task = progress.add_task(
                "Building tree...",
                total=total_skills,
                pending=0,
                leaf=0,
                llm=0,
                total_count=total_skills)

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                self._executor = executor
                while queue or futures_map:
                    # Submit all queued tasks
                    while queue:
                        node, node_skills, depth, parent_context = queue.pop(0)
                        future = executor.submit(
                            self._process_node,
                            node=node,
                            skills=node_skills,
                            depth=depth,
                            parent_context=parent_context,
                            verbose=verbose,
                        )
                        futures_map[future] = (node, depth)

                    # Update pending count
                    if self._progress and self._progress_task is not None:
                        self._progress.update(self._progress_task, pending=len(futures_map))

                    if not futures_map:
                        break

                    # Wait for at least one to complete
                    done, _ = wait(futures_map.keys(), return_when=FIRST_COMPLETED)

                    # Process completed futures
                    def future_node_id(fut) -> str:
                        entry = futures_map.get(fut)
                        return entry[0].id if entry is not None else ""

                    for future in sorted(done, key=future_node_id):
                        node, depth = futures_map.pop(future)
                        try:
                            children_to_process = future.result()
                            # Add children to queue
                            for child_node, child_skills in children_to_process:
                                parent_ctx = {"name": child_node.name, "description": child_node.description}
                                queue.append((child_node, child_skills, depth + 1, parent_ctx))
                        except Exception as e:
                            console.print(f"[red]Error processing {node.id}: {e}[/red]")

            self._progress = None
            self._executor = None

        # Global audit: check for missing skills before any post-process repair.
        self._audit_tree(root, skills)

        # Post-build repair: reassign obviously misplaced skills between sibling subtrees.
        if self._postprocess_enabled and self._postprocess_max_passes > 0:
            self._postprocess_tree(root, verbose)

        # Normalize second-leaf nodes into equivalence-group form.
        if self._equiv_grouping_enabled:
            self._normalize_to_equivalence_groups(root, verbose)

        # Final audit after post-process to ensure no skills were lost during repair.
        self._audit_tree(root, skills)

        return root

    def _collect_leaf_skills(self, node: TreeNode) -> set:
        """Recursively collect all skill IDs from leaf nodes."""
        if node.is_leaf:
            return {s.id for s in node.skills}
        result = set()
        for child in node.children:
            result |= self._collect_leaf_skills(child)
        return result

    def _audit_tree(self, root: TreeNode, input_skills: list[dict]) -> None:
        """Post-build audit: recover any missing skills into an 'uncategorized' node."""
        input_ids = {s["id"] for s in input_skills}
        tree_ids = self._collect_leaf_skills(root)
        missing_ids = input_ids - tree_ids

        if not missing_ids:
            return

        console.print(Panel(
            f"[bold red]Skill Loss Detected: {len(missing_ids)}/{len(input_ids)} skills "
            f"missing from tree.[/bold red]\n"
            "Recovering into 'uncategorized' node.",
            title="[bold red]Post-Build Audit[/bold red]",
            border_style="red",
        ))

        # Build uncategorized node
        skill_map = {s["id"]: s for s in input_skills}
        missing_skills = [skill_map[sid] for sid in missing_ids if sid in skill_map]

        uncat_node = TreeNode(
            id="uncategorized",
            name="Uncategorized",
            description="Skills that were lost during classification and recovered by post-build audit.",
            depth=1,
            parent_id="root",
        )
        self._assign_skills_to_leaf(uncat_node, missing_skills)
        root.children.append(uncat_node)

    def _process_node(
        self,
        node: TreeNode,
        skills: list[dict],
        depth: int,
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> list[tuple[TreeNode, list[dict]]]:
        """
        Process a single node: split skills and create children.
        Returns list of (child_node, child_skills) tuples for further processing.
        """
        # Special handling for root node: use fixed categories with flat mapping
        if depth == 0 and node.id == "root":
            # Phase 1: Use custom root categories if configured, otherwise fixed defaults
            categories = self.config.root_categories or FIXED_ROOT_CATEGORIES
            groups = {cat_id: {"name": d["name"], "description": d["description"]}
                      for cat_id, d in categories.items()}
            if self._progress and self._progress_task is not None:
                self._progress.update(self._progress_task,
                                      description=f"Classifying root ({len(skills)} skills)")
            if verbose:
                console.print(f"[cyan]Classifying {len(skills)} skills into fixed root categories[/cyan]")
            # Phase 2: Classify via flat mapping
            assignments = self._classify_skills(skills, groups, verbose)
            assignments = self._validate_and_recover(skills, groups, assignments, verbose)
            groups_with_skills = self._build_groups_from_assignments(groups, assignments)
            return self._build_children_from_groups(node, skills, groups_with_skills, depth, verbose)

        # Terminal condition: skills count within threshold
        if len(skills) <= self.config.max_skills_per_node:
            self._assign_skills_to_leaf(node, skills)
            if verbose:
                console.print(f"[dim]  Leaf: {node.id} ({len(skills)} skills)[/dim]")
            return []

        # Depth limit check
        if depth >= self.config.max_depth:
            console.print(Panel(
                f"[bold red]Max depth ({self.config.max_depth}) reached at node '{node.id}' "
                f"with {len(skills)} skills remaining.[/bold red]\n"
                "These skills will be forced into a single leaf node.",
                title="[bold red]Max Depth Reached[/bold red]",
                border_style="red",
            ))
            self._assign_skills_to_leaf(node, skills)
            return []

        # LLM grouping
        if self._progress and self._progress_task is not None:
            self._progress.update(self._progress_task, description=f"Splitting: {node.id} ({len(skills)} skills)")
        if verbose:
            console.print(f"[cyan]Splitting: {node.id} ({len(skills)} skills, depth={depth})[/cyan]")

        groups = self._split_skills(skills, parent_context, verbose)

        if not groups:
            # Grouping failed, make it a leaf
            console.print(Panel(
                f"[bold red]Grouping failed for node '{node.id}' with {len(skills)} skills.[/bold red]\n"
                "All skills will be forced into a single leaf node.",
                title="[bold red]Grouping Failed[/bold red]",
                border_style="red",
            ))
            self._assign_skills_to_leaf(node, skills)
            return []

        return self._build_children_from_groups(node, skills, groups, depth, verbose)

    # =========================================================================
    # Two-phase classification: discover groups -> flat assignment
    # =========================================================================

    def _assign_skills_to_leaf(self, node: TreeNode, skills: list[dict]) -> None:
        """Assign skill dicts to a leaf node as Skill objects. Updates progress counter."""
        for skill_data in skills:
            node.skills.append(self._skill_from_data(skill_data, path=node.id))
        # Update leaf skills count (thread-safe)
        with self._counter_lock:
            self._leaf_skills += len(skills)
            if self._progress and self._progress_task is not None:
                self._progress.update(self._progress_task, leaf=self._leaf_skills, completed=self._leaf_skills)

    @staticmethod
    def _skill_from_data(skill_data: dict, *, path: str) -> Skill:
        """Materialize a Skill object from a skill dict."""
        return Skill(
            id=skill_data["id"],
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
    def _skill_to_data(skill: Skill) -> dict:
        """Convert a Skill object back into a mutable skill dict."""
        return skill.to_dict(include_content=True)

    def _build_children_from_groups(
        self,
        node: TreeNode,
        skills: list[dict],
        groups: dict,
        depth: int,
        verbose: bool = False,
    ) -> list[tuple[TreeNode, list[dict]]]:
        """Build child nodes from groups dict. Returns [(child_node, child_skills)]."""
        skill_map = {s["id"]: s for s in skills}
        children_to_process = []
        singleton_triggered = False

        for group_id, group_data in groups.items():
            child_skill_ids = group_data.get("skill_ids", [])
            child_skills = [skill_map[sid] for sid in child_skill_ids if sid in skill_map]

            # Empty group is invalid.
            if not child_skills:
                continue

            # Optionally skip singleton groups and reassign their skills in a later pass.
            if len(child_skills) < 2 and not self._equiv_allow_singleton_groups:
                if len(child_skills) == 1:
                    singleton_triggered = True
                if verbose:
                    skipped_ids = [s["id"] for s in child_skills]
                    console.print(f"[dim]  Skipping singleton group '{group_id}' "
                                  f"({skipped_ids}), will reassign into existing groups[/dim]")
                continue

            child_node = TreeNode(
                id=group_id,
                name=group_data.get("name", group_id),
                description=group_data.get("description", ""),
                depth=depth + 1,
                parent_id=node.id,
            )

            node.children.append(child_node)

            # Mark assigned skills
            for sid in child_skill_ids:
                skill_map.pop(sid, None)

            children_to_process.append((child_node, child_skills))

        # Handle unassigned skills
        if skill_map:
            if children_to_process:
                unassigned = list(skill_map.values())
                reassigned_count, unassigned = self._reassign_skills_to_children(
                    unassigned,
                    children_to_process,
                )

                if unassigned:
                    largest_idx = max(range(len(children_to_process)),
                                      key=lambda i: len(children_to_process[i][1]))
                    unassigned_ratio = len(unassigned) / len(skills) if skills else 0
                    if unassigned_ratio > 0.1:
                        console.print(Panel(
                            f"[bold red]{len(unassigned)}/{len(skills)} skills ({unassigned_ratio:.0%}) unassigned "
                            f"at node '{node.id}'[/bold red]\n"
                            f"Dumping into '{children_to_process[largest_idx][0].id}'.",
                            title="[bold red]High Unassigned Skill Count[/bold red]",
                            border_style="red",
                        ))
                    elif verbose:
                        console.print(f"[yellow]  {len(unassigned)} unassigned skills -> "
                                      f"{children_to_process[largest_idx][0].id}[/yellow]")
                    child_node, child_skills = children_to_process[largest_idx]
                    children_to_process[largest_idx] = (child_node, child_skills + unassigned)
                elif verbose and reassigned_count > 0:
                    console.print(f"[dim]  Reassigned {reassigned_count} skipped skills under '{node.id}'[/dim]")
            else:
                # All groups invalid -> leaf fallback
                self._assign_skills_to_leaf(node, skills)
                return []

        if singleton_triggered and children_to_process:
            self._rewrite_node_label_after_singleton(node, children_to_process, verbose)

        return children_to_process

    def _reassign_skills_to_children(
        self,
        unassigned_skills: list[dict],
        children_to_process: list[tuple[TreeNode, list[dict]]],
    ) -> tuple[int, list[dict]]:
        """Reassign skipped/unassigned skills to existing child groups via flat mapping."""
        if not unassigned_skills or not children_to_process:
            return 0, unassigned_skills

        groups = {
            child_node.id: {
                "name": child_node.name,
                "description": child_node.description,
            }
            for child_node, _ in children_to_process
        }
        assignments = self._classify_skills_single(
            self._sorted_skills(unassigned_skills),
            groups,
            verbose=False,
            is_retry=True,
        )
        if not assignments:
            return 0, unassigned_skills

        child_idx = {child_node.id: idx for idx, (child_node, _) in enumerate(children_to_process)}
        reassigned_count = 0
        remaining_unassigned = []

        for skill in unassigned_skills:
            group_id = assignments.get(skill["id"])
            idx = child_idx.get(group_id)
            if idx is None:
                remaining_unassigned.append(skill)
                continue
            _, child_skills = children_to_process[idx]
            child_skills.append(skill)
            reassigned_count += 1

        return reassigned_count, remaining_unassigned

    def _rewrite_node_label_after_singleton(
        self,
        node: TreeNode,
        children_to_process: list[tuple[TreeNode, list[dict]]],
        verbose: bool = False,
    ) -> None:
        """Rewrite current node name/description after singleton reassignment."""
        if not children_to_process:
            return

        summary_lines = []
        for child_node, child_skills in sorted(children_to_process, key=lambda item: len(item[1]), reverse=True):
            sample_ids = ", ".join(skill["id"] for skill in child_skills[:5]) or "(none)"
            child_desc = child_node.description or "(no description)"
            summary_lines.append(
                f"- {child_node.id} ({len(child_skills)} skills)\n"
                f"  name: {child_node.name}\n"
                f"  description: {child_desc}\n"
                f"  sample_skill_ids: {sample_ids}"
            )

        prompt = NODE_LABEL_REWRITE_PROMPT.format(
            node_id=node.id,
            node_name=node.name,
            node_description=node.description or "(no description)",
            children_summary="\n".join(summary_lines),
        )
        result = self._call_llm_json(prompt)
        new_name = str(result.get("name", "")).strip()
        new_description = str(result.get("description", "")).strip()

        if not new_name or not new_description:
            if verbose:
                console.print(f"[yellow]  Failed to rewrite label for '{node.id}', keeping original[/yellow]")
            return

        node.name = new_name
        node.description = new_description
        if verbose:
            console.print(f"[dim]  Rewrote node label for '{node.id}' after singleton reassignment[/dim]")

    def _postprocess_tree(self, root: TreeNode, verbose: bool = False) -> None:
        """Run one or more conservative repair passes after the initial tree is built."""
        total_reassignments = 0
        for repair_pass in range(1, self._postprocess_max_passes + 1):
            moved = self._postprocess_node(root, verbose=verbose)
            total_reassignments += moved
            if verbose:
                console.print(
                    f"[dim]  Post-process pass {repair_pass}: reassigned {moved} skills[/dim]"
                )
            if moved <= 0:
                break

        if total_reassignments > 0:
            console.print(
                Panel(
                    (
                        f"[bold green]Post-process repaired {total_reassignments} "
                        "misplaced skill assignments.[/bold green]"
                    ),
                    title="[bold green]Tree Repair[/bold green]",
                    border_style="green",
                )
            )

    def _postprocess_node(self, node: TreeNode, verbose: bool = False) -> int:
        """Bottom-up repair pass for one subtree."""
        if node.is_leaf:
            return 0

        moved = 0
        for child in list(node.children):
            moved += self._postprocess_node(child, verbose=verbose)
        moved += self._rebalance_child_assignments(node, verbose=verbose)
        return moved

    def _rebalance_child_assignments(self, node: TreeNode, verbose: bool = False) -> int:
        """
        Re-score all descendant skills against the current direct children and repair
        obviously misplaced assignments.
        """
        if len(node.children) < 2:
            return 0

        groups = {
            child.id: {
                "name": child.name,
                "description": child.description,
            }
            for child in node.children
        }
        if len(groups) < 2:
            return 0

        skill_entries: list[dict] = []
        skill_data_by_id: dict[str, dict] = {}
        source_leaf_by_skill_id: dict[str, TreeNode] = {}
        source_child_by_skill_id: dict[str, str] = {}

        for child in node.children:
            for leaf_node, skill_data in self._collect_subtree_skill_locations(child):
                skill_id = str(skill_data.get("id", "")).strip()
                if not skill_id:
                    continue
                skill_entries.append(skill_data)
                skill_data_by_id[skill_id] = skill_data
                source_leaf_by_skill_id[skill_id] = leaf_node
                source_child_by_skill_id[skill_id] = child.id

        if len(skill_entries) < self._postprocess_min_skills:
            return 0

        assignments = self._classify_skills(skill_entries, groups, verbose=False)
        assignments = self._validate_and_recover(skill_entries, groups, assignments, verbose=False)

        child_by_id = {child.id: child for child in node.children}
        moves: list[tuple[str, str]] = []
        for skill_id, current_child_id in source_child_by_skill_id.items():
            target_child_id = assignments.get(skill_id)
            if not target_child_id or target_child_id == current_child_id:
                continue
            if target_child_id not in child_by_id:
                continue
            moves.append((skill_id, target_child_id))

        if not moves:
            return 0

        for skill_id, target_child_id in moves:
            source_leaf = source_leaf_by_skill_id.get(skill_id)
            skill_data = skill_data_by_id.get(skill_id)
            target_child = child_by_id.get(target_child_id)
            if source_leaf is None or skill_data is None or target_child is None:
                continue
            source_leaf.skills = [skill for skill in source_leaf.skills if skill.id != skill_id]
            self._insert_skill_into_subtree(target_child, skill_data)

        removed_empty = self._prune_empty_children(node)
        reassigned_tiny = self._repair_small_leaf_children(node)

        if removed_empty or reassigned_tiny:
            self._prune_empty_children(node)
            if len(node.children) >= 2:
                self._rewrite_node_label_after_singleton(
                    node,
                    [
                        (child, self._collect_subtree_skill_dicts(child))
                        for child in node.children
                        if child.count_all_skills() > 0
                    ],
                    verbose=verbose,
                )

        total_moved = len(moves) + reassigned_tiny
        if verbose and total_moved > 0:
            console.print(
                f"[dim]  Post-process repaired '{node.id}': moved={len(moves)}, "
                f"tiny_group_reassigned={reassigned_tiny}, removed_empty={removed_empty}[/dim]"
            )
        return total_moved

    def _collect_subtree_skill_locations(self, node: TreeNode) -> list[tuple[TreeNode, dict]]:
        """Return [(leaf_node, skill_dict)] for every skill under the subtree."""
        if node.is_leaf:
            return [(node, self._skill_to_data(skill)) for skill in node.skills]

        results: list[tuple[TreeNode, dict]] = []
        for child in node.children:
            results.extend(self._collect_subtree_skill_locations(child))
        return results

    def _collect_subtree_skill_dicts(self, node: TreeNode) -> list[dict]:
        """Collect all skills under a subtree as plain dicts."""
        return [skill_data for _, skill_data in self._collect_subtree_skill_locations(node)]

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
        assignment = self._classify_skills_single(
            [skill_data],
            groups,
            verbose=False,
            is_retry=True,
        )
        child_id = assignment.get(str(skill_data.get("id", "")).strip())
        if child_id in child_by_id:
            return child_by_id[child_id]
        return max(children, key=lambda child: child.count_all_skills())

    def _insert_skill_into_subtree(self, node: TreeNode, skill_data: dict) -> None:
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
        self._insert_skill_into_subtree(target_child, skill_data)

    def _prune_empty_children(self, node: TreeNode) -> int:
        """Remove empty child subtrees after skill moves."""
        removed = 0
        kept_children: list[TreeNode] = []
        for child in node.children:
            removed += self._prune_empty_children(child)
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

    def _repair_small_leaf_children(self, node: TreeNode) -> int:
        """
        Merge direct leaf children with <2 skills back into their siblings.
        This keeps post-process from leaving obviously unstable tiny groups behind.
        """
        if self._equiv_allow_singleton_groups:
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

        reassigned_skills = [self._skill_to_data(skill) for child in tiny_leaf_children for skill in child.skills]
        node.children = remaining_children
        for skill_data in reassigned_skills:
            target_child = self._choose_child_for_skill(skill_data, node.children)
            self._insert_skill_into_subtree(target_child, skill_data)
        return len(reassigned_skills)

    def _normalize_to_equivalence_groups(self, root: TreeNode, verbose: bool = False) -> None:
        """
        Convert second-leaf nodes into equivalence-group form.

        For any node whose direct child is a second-leaf node (i.e., child.children are all leaves),
        we regroup that child's leaves into one or more equivalence groups and replace the child with
        the resulting groups at the same parent depth.
        """
        if root.is_leaf:
            return

        updated_children: list[TreeNode] = []
        split_count = 0
        for child in list(root.children):
            self._normalize_to_equivalence_groups(child, verbose=verbose)
            if root.id != "root" and self._is_second_leaf_node(child):
                replacement_nodes = self._split_second_leaf_node_into_equiv_groups(
                    root, child, verbose=verbose
                )
                updated_children.extend(replacement_nodes)
                if len(replacement_nodes) > 1 or replacement_nodes[0].id != child.id:
                    split_count += 1
            else:
                updated_children.append(child)

        root.children = updated_children
        if verbose and split_count > 0:
            console.print(
                f"[dim]  Equivalence regrouping updated {split_count} second-leaf nodes under '{root.id}'[/dim]"
            )

    @staticmethod
    def _is_second_leaf_node(node: TreeNode) -> bool:
        """Second-leaf node: has children and all children are leaf nodes."""
        if not node.children:
            return False
        return all(child.is_leaf for child in node.children)

    def _split_second_leaf_node_into_equiv_groups(
        self,
        parent_node: TreeNode,
        second_leaf_node: TreeNode,
        verbose: bool = False,
    ) -> list[TreeNode]:
        """
        Split a second-leaf node into one or more sibling equivalence-group nodes.

        Returns replacement nodes at the same depth as second_leaf_node.
        """
        leaf_children = list(second_leaf_node.children)
        if len(leaf_children) <= 1:
            return [second_leaf_node]

        groups = self._discover_equivalence_groups(second_leaf_node, leaf_children, verbose=verbose)
        if not groups:
            return [second_leaf_node]

        normalized_groups = self._normalize_equivalence_groups(leaf_children, groups)
        if len(normalized_groups) <= 1:
            only_group = normalized_groups[0]
            second_leaf_node.name = only_group.get("name", second_leaf_node.name)
            second_leaf_node.description = only_group.get("description", second_leaf_node.description)
            return [second_leaf_node]

        used_ids = {child.id for child in parent_node.children}
        replacement_nodes: list[TreeNode] = []
        for idx, group in enumerate(normalized_groups, start=1):
            base_id = self._build_equivalence_group_id(
                group_id=str(group.get("id") or "").strip(),
                group_name=str(group.get("name") or "").strip(),
                fallback=f"{second_leaf_node.id}-equiv-{idx}",
            )
            group_id = base_id
            suffix = 2
            while group_id in used_ids:
                group_id = f"{base_id}-{suffix}"
                suffix += 1
            used_ids.add(group_id)

            new_node = TreeNode(
                id=group_id,
                name=str(group.get("name") or group_id),
                description=str(group.get("description") or second_leaf_node.description),
                depth=second_leaf_node.depth,
                parent_id=second_leaf_node.parent_id,
            )
            for leaf in group.get("leaf_nodes", []):
                leaf.parent_id = new_node.id
                leaf.depth = new_node.depth + 1
                new_node.children.append(leaf)
            replacement_nodes.append(new_node)

        if verbose:
            console.print(
                f"[dim]  Split '{second_leaf_node.id}' into {len(replacement_nodes)} equivalence groups[/dim]"
            )
        return replacement_nodes

    def _discover_equivalence_groups(
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
        result = self._call_llm_json(prompt)
        groups = result.get("groups", {})
        if not isinstance(groups, dict):
            if verbose:
                console.print(f"[yellow]  Equivalence grouping failed for '{second_leaf_node.id}'[/yellow]")
            return {}
        return groups

    def _normalize_equivalence_groups(self, leaf_children: list[TreeNode], groups: dict) -> list[dict]:
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
                    "id": self._build_equivalence_group_id(
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
        if missing and normalized and not self._equiv_allow_singleton_groups:
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
        if self._deterministic_prompts:
            for item in normalized:
                item["leaf_nodes"] = sorted(item["leaf_nodes"], key=lambda leaf: leaf.id)
            normalized.sort(key=lambda item: str(item.get("id", "")))
        return normalized

    def _build_equivalence_group_id(self, *, group_id: str, group_name: str, fallback: str) -> str:
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

    def _sorted_skills(self, skills: list[dict]) -> list[dict]:
        """Return skills in deterministic order when enabled."""
        if not self._deterministic_prompts:
            return list(skills)
        return sorted(skills, key=lambda s: str(s.get("id", "")))

    def _iter_group_items(self, groups: dict):
        """Iterate group items in deterministic order when enabled."""
        if not self._deterministic_prompts:
            return groups.items()
        return ((gid, groups[gid]) for gid in sorted(groups.keys()))

    @staticmethod
    def _normalize_prompt_for_fingerprint(prompt: str) -> str:
        """Normalize prompt text to keep fingerprint stable across runs."""
        normalized = prompt.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
        return normalized.strip()

    def _prompt_fingerprint(self, prompt: str) -> str:
        """Compute deterministic prompt fingerprint."""
        payload = (
            f"{self._prompt_fingerprint_version}\n"
            f"{self.model}\n"
            f"{self._normalize_prompt_for_fingerprint(prompt)}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _sampling_seed(self, parent_context: Optional[dict], skills_count: int) -> int:
        """Derive deterministic sampling seed from node context and size."""
        parent_name = (parent_context or {}).get("name", "")
        parent_desc = (parent_context or {}).get("description", "")
        material = f"{self._discovery_seed}|{parent_name}|{parent_desc}|{skills_count}"
        seed_hex = hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]
        return int(seed_hex, 16)

    def _extract_cache_hit(self, response) -> Optional[bool]:
        """Best-effort extraction of cache hit status from LiteLLM response."""
        containers = []
        hidden_params = getattr(response, "_hidden_params", None)
        if isinstance(hidden_params, dict):
            containers.append(hidden_params)

        response_headers = getattr(response, "_response_headers", None)
        if isinstance(response_headers, dict):
            containers.append(response_headers)

        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()  # pydantic-like response
            if isinstance(dumped, dict):
                containers.append(dumped)

        for mapping in containers:
            hit = self._extract_cache_hit_from_mapping(mapping)
            if hit is not None:
                return hit
        return None

    @staticmethod
    def _extract_cache_hit_from_mapping(mapping: dict) -> Optional[bool]:
        """Parse cache hit from a mapping (recursively over nested dicts)."""
        key_aliases = {
            "cache_hit",
            "cachehit",
            "is_cached",
            "cached",
            "x-litellm-cache-hit",
            "litellm_cache_hit",
        }
        stack = [mapping]
        while stack:
            current = stack.pop()
            if not isinstance(current, dict):
                continue
            for key, value in current.items():
                key_norm = str(key).strip().lower()
                if key_norm in key_aliases:
                    if isinstance(value, bool):
                        return value
                    if isinstance(value, (int, float)):
                        return bool(value)
                    if isinstance(value, str):
                        value_norm = value.strip().lower()
                        if value_norm in {"1", "true", "hit", "yes"}:
                            return True
                        if value_norm in {"0", "false", "miss", "no"}:
                            return False
                if isinstance(value, dict):
                    stack.append(value)
        return None

    def _record_cache_observation(self, cache_hit: Optional[bool]) -> None:
        """Aggregate cache hit/miss counters."""
        if cache_hit is True:
            self._cache_hits += 1
        elif cache_hit is False:
            self._cache_misses += 1
        else:
            self._cache_unknown += 1

    def _print_cache_stats(self) -> None:
        """Print cache observability metrics for intuitive build feedback."""
        if not self._cache_observability:
            return
        known_total = self._cache_hits + self._cache_misses
        observed_hit_rate = (self._cache_hits / known_total * 100.0) if known_total else 0.0
        lower_bound_hit_rate = (self._cache_hits / self._llm_calls * 100.0) if self._llm_calls else 0.0
        lines = [
            f"LLM calls: {self._llm_calls}",
            f"Retry calls: {self._retry_calls}",
            f"Cache hits/misses/unknown: {self._cache_hits}/{self._cache_misses}/{self._cache_unknown}",
            f"Observed hit rate (known only): {observed_hit_rate:.1f}%",
            f"Estimated hit rate lower bound: {lower_bound_hit_rate:.1f}%",
            f"Unique prompt fingerprints: {len(self._prompt_fingerprints)}",
        ]
        console.print(Panel("\n".join(lines), title="[bold cyan]Cache Stats[/bold cyan]", border_style="cyan"))

    def _build_groups_from_assignments(self, groups: dict, assignments: dict) -> dict:
        """Convert flat {skill_id: group_id} back to groups dict with skill_ids lists."""
        result = {}
        for gid, gdata in self._iter_group_items(groups):
            sids = [sid for sid, assigned_gid in assignments.items() if assigned_gid == gid]
            if self._deterministic_prompts:
                sids.sort()
            if sids:
                result[gid] = {
                    "name": gdata.get("name", gid),
                    "description": gdata.get("description", ""),
                    "skill_ids": sids,
                }
        return result

    def _classify_skills(self, skills: list[dict], groups: dict, verbose: bool = False) -> dict:
        """
        Universal Phase 2: assign each skill to a group via flat mapping.

        Args:
            skills: skill dicts to classify
            groups: {group_id: {"name": ..., "description": ...}}
        Returns:
            {skill_id: group_id} mapping
        """
        ordered_skills = self._sorted_skills(skills)
        batch_size = self._auto_batch_size()
        if len(ordered_skills) > batch_size:
            return self._batched_classify_skills(ordered_skills, groups, batch_size, verbose)
        return self._classify_skills_single(ordered_skills, groups, verbose)

    def _classify_skills_single(
        self,
        skills: list[dict],
        groups: dict,
        verbose: bool = False,
        is_retry: bool = False,
    ) -> dict:
        """Single LLM call to assign skills to groups. Returns {skill_id: group_id}."""
        groups_list = "\n".join(
            f"- {gid}: {g['name']}\n  {g['description']}" for gid, g in self._iter_group_items(groups)
        )
        skills_list = self._format_skills_list(skills)
        prompt = SKILL_ASSIGNMENT_PROMPT.format(groups_list=groups_list, skills_list=skills_list)
        result = self._call_llm_json(prompt, is_retry=is_retry)
        raw = result.get("assignments", {})
        valid_groups = set(groups.keys())
        valid_skills = {s["id"] for s in skills}

        # Normalize group IDs: lowercase, strip, underscores -> hyphens
        def normalize_gid(gid):
            normed = gid.strip().lower().replace("_", "-")
            if normed in valid_groups:
                return normed
            return gid  # return original if normalization doesn't help

        return {sid: normalize_gid(gid)
                for sid, gid in raw.items()
                if sid in valid_skills and normalize_gid(gid) in valid_groups}

    def _batched_classify_skills(self, skills: list[dict], groups: dict,
                                 batch_size: int, verbose: bool = False) -> dict:
        """Parallel batched assignment. All batches use the same groups."""
        ordered_skills = self._sorted_skills(skills)
        batches = [ordered_skills[i:i + batch_size] for i in range(0, len(ordered_skills), batch_size)]
        all_assignments = {}
        executor = self._executor
        if executor is None:
            # Fallback: no shared executor available (shouldn't happen in normal flow)
            for batch in batches:
                try:
                    all_assignments.update(self._classify_skills_single(batch, groups, verbose))
                except Exception as e:
                    console.print(f"[red]Classification batch failed: {e}[/red]")
            return all_assignments
        futures = {
            executor.submit(self._classify_skills_single, batch, groups, verbose): i
            for i, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            try:
                all_assignments.update(future.result())
            except Exception as e:
                console.print(f"[red]Classification batch failed: {e}[/red]")
        return all_assignments

    def _validate_and_recover(self, skills: list[dict], groups: dict,
                              assignments: dict, verbose: bool = False) -> dict:
        """Validate assignments, retry missing, fallback remaining to largest group."""
        skill_ids = {s["id"] for s in skills}
        assigned_ids = set(assignments.keys())
        missing = skill_ids - assigned_ids

        if not missing:
            return assignments

        # 100% failure: don't retry, return empty to let caller handle as grouping failure
        if not assignments:
            console.print(Panel(
                f"[bold red]Classification returned 0 assignments for {len(skills)} skills.[/bold red]",
                title="[bold red]Classification Failed[/bold red]",
                border_style="red",
            ))
            return assignments  # empty dict -> caller treats as grouping failure

        # Warn when >30% missing (even if retry will fix it)
        if len(missing) / len(skills) > 0.3:
            console.print(f"[yellow]  Warning: {len(missing)}/{len(skills)} skills unassigned before retry[/yellow]")

        # Retry unassigned (only if <= 50% missing)
        if len(missing) <= len(skills) * 0.5:
            missing_skills = self._sorted_skills([s for s in skills if s["id"] in missing])
            console.print(f"[yellow]  Retrying {len(missing)} unassigned skills...[/yellow]")
            retry = self._classify_skills_single(missing_skills, groups, verbose, is_retry=True)
            assignments.update(retry)
            missing = skill_ids - set(assignments.keys())

        # Final fallback: largest group
        if missing:
            largest = max(groups, key=lambda g: sum(1 for v in assignments.values() if v == g))
            for sid in missing:
                assignments[sid] = largest
            if len(missing) / len(skills) > 0.1:
                console.print(Panel(
                    f"[bold red]{len(missing)}/{len(skills)} skills unassigned after retry, "
                    f"forced into '{largest}'[/bold red]",
                    title="[bold red]Classification Recovery[/bold red]",
                    border_style="red",
                ))

        return assignments

    def _discover_groups(self, skills: list[dict], parent_context: Optional[dict],
                         verbose: bool = False) -> dict:
        """Phase 1: Discover group definitions from skills (no assignment)."""
        if parent_context:
            context_section = (
                f'## Parent Context\n'
                f'You are creating sub-categories under "{parent_context["name"]}": '
                f'{parent_context["description"]}\n'
                f'Ensure sub-categories are coherent with this parent context.'
            )
        else:
            context_section = "## Context\nYou are creating top-level categories for all skills."

        min_groups = max(2, self.config.branching_factor - 3)
        max_groups = self.config.branching_factor + 2
        skills_list = self._format_skills_list(skills)

        prompt = GROUP_DISCOVERY_PROMPT.format(
            count=len(skills),
            context_section=context_section,
            skills_list=skills_list,
            min_groups=min_groups,
            max_groups=max_groups,
        )
        result = self._call_llm_json(prompt)
        groups = result.get("groups", {})
        # Return only definitions (name + description), strip any skill_ids
        return {
            gid: {"name": g.get("name", gid), "description": g.get("description", "")}
            for gid, g in self._iter_group_items(groups)
        }

    def _merge_group_definitions(self, all_group_defs: list[dict], verbose: bool = False) -> dict:
        """Merge group definitions from multiple discovery samples. No skill IDs involved."""
        if verbose:
            console.print(f"[cyan]    Merging group definitions from {len(all_group_defs)} samples[/cyan]")

        all_groups_text = []
        for i, group_defs in enumerate(all_group_defs):
            lines = [f"### Sample {i+1}"]
            for gid, gdata in self._iter_group_items(group_defs):
                lines.append(f"- {gid}: {gdata.get('name', gid)}")
                if gdata.get('description'):
                    lines.append(f"  {gdata['description']}")
            all_groups_text.append("\n".join(lines))

        min_groups = max(2, self.config.branching_factor - 3)
        max_groups = self.config.branching_factor + 2
        prompt = GROUP_MERGE_PROMPT.format(
            all_groups="\n\n".join(all_groups_text),
            min_groups=min_groups, max_groups=max_groups)
        result = self._call_llm_json(prompt)

        # Extract unified group definitions (no skill_ids to remap)
        canonical = result.get("canonical_groups", {})
        return {
            gid: {"name": g.get("name", gid), "description": g.get("description", "")}
            for gid, g in self._iter_group_items(canonical)
        }

    # =========================================================================
    # Skill splitting (recursive layer)
    # =========================================================================

    def _split_skills(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        """Split skills into groups. Auto-batches for large sets."""
        batch_size = self._auto_batch_size()
        if len(skills) > batch_size:
            return self._batched_split_skills(skills, parent_context, batch_size, verbose)
        return self._split_skills_single(skills, parent_context, verbose)

    def _split_skills_single(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        """Split skills into groups using two-phase approach: discover groups then classify."""
        # Phase 1: Discover groups (output-light, ~300 tokens)
        groups = self._discover_groups(skills, parent_context, verbose)
        if not groups:
            return {}

        # Phase 2: Classify (output-light, flat mapping)
        assignments = self._classify_skills(skills, groups, verbose)
        assignments = self._validate_and_recover(skills, groups, assignments, verbose)

        return self._build_groups_from_assignments(groups, assignments)

    def _batched_split_skills(self, skills, parent_context, batch_size, verbose=False):
        """Split large skill set: multi-sample discovery + definition merge + parallel assignment."""
        if verbose:
            console.print(f"[cyan]  Batched split: {len(skills)} skills, batch_size={batch_size}[/cyan]")

        # Phase 1: Discover groups from sampled subsets
        if len(skills) <= batch_size:
            # Single sample covers all skills
            groups = self._discover_groups(skills, parent_context, verbose)
        else:
            # Multiple samples -> discover independently -> merge definitions
            shuffled = self._sorted_skills(skills)
            if self._deterministic_prompts:
                rng = random.Random(self._sampling_seed(parent_context, len(skills)))
                rng.shuffle(shuffled)
            else:
                random.shuffle(shuffled)
            samples = [shuffled[i:i + batch_size] for i in range(0, len(shuffled), batch_size)]
            # Cap discovery rounds: up to 5 for better coverage (parallel, same wall-clock)
            discovery_samples = samples[:min(5, len(samples))]

            all_group_defs = []
            executor = self._executor
            if executor is None:
                # Fallback: no shared executor available
                for sample in discovery_samples:
                    try:
                        result = self._discover_groups(sample, parent_context, verbose)
                        if result:
                            all_group_defs.append(result)
                    except Exception as e:
                        console.print(f"[red]Discovery batch failed: {e}[/red]")
            else:
                futures = {
                    executor.submit(self._discover_groups, sample, parent_context, verbose): i
                    for i, sample in enumerate(discovery_samples)
                }
                indexed_group_defs = []
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            indexed_group_defs.append((futures[future], result))
                    except Exception as e:
                        console.print(f"[red]Discovery batch failed: {e}[/red]")
                indexed_group_defs.sort(key=lambda item: item[0])
                all_group_defs = [result for _, result in indexed_group_defs]

            if not all_group_defs:
                return {}
            if len(all_group_defs) == 1:
                groups = all_group_defs[0]
            else:
                # Merge group DEFINITIONS only (lightweight, no skill IDs involved)
                groups = self._merge_group_definitions(all_group_defs, verbose)

        if not groups:
            return {}

        # Phase 2: All skills assign to SAME unified groups (parallel batched)
        assignments = self._classify_skills(skills, groups, verbose)
        assignments = self._validate_and_recover(skills, groups, assignments, verbose)

        return self._build_groups_from_assignments(groups, assignments)

    # =========================================================================
    # Helper methods
    # =========================================================================

    def _call_llm(self, prompt: str, is_retry: bool = False, retry_left: int | None = None) -> str:
        """Call LLM and return response with semaphore and circuit-breaker protections."""
        if self._client is None:
            raise RuntimeError(
                "openai is required to build the tree. "
                "Please install the openai package first."
            )
        mcfg = self._manager_config
        if retry_left is None:
            retry_left = int(mcfg.build.num_retries)
        max_tokens = self._get_max_output_tokens()
        prompt_fingerprint = self._prompt_fingerprint(prompt)
        with self._counter_lock:
            self._llm_calls += 1
            if is_retry:
                self._retry_calls += 1
            if self._cache_observability:
                self._prompt_fingerprints.add(prompt_fingerprint)
            if self._progress and self._progress_task is not None:
                self._progress.update(self._progress_task, llm=self._llm_calls)
        try:
            with self._llm_semaphore:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    timeout=mcfg.build.timeout,
                    extra_body=self._merged_extra_body(),
                )
            # Detect output truncation
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                self._thread_local.truncated = True
                console.print(Panel(
                    "[bold red]OUTPUT TRUNCATED![/bold red]\n"
                    f"The LLM response was cut off at {max_tokens} tokens (finish_reason='length').\n"
                    "This will cause incomplete JSON parsing and skill loss.\n"
                    "Consider reducing batch size or increasing max_tokens.",
                    title="[bold red]Truncation Warning[/bold red]",
                    border_style="red",
                ))
            else:
                self._thread_local.truncated = False
            # Reset consecutive failure counter on success
            with self._counter_lock:
                self._consecutive_failures = 0
                if self._cache_observability:
                    self._record_cache_observation(None)
            return response.choices[0].message.content or "{}"
        except Exception as e:
            if AuthenticationError is not None and isinstance(e, AuthenticationError):
                console.print("[red]Authentication failed - check API key[/red]")
                raise
            err_text = str(e).lower()
            is_context_exceeded = any(
                marker in err_text
                for marker in ("context length", "maximum context", "too many tokens", "max context")
            )
            if is_context_exceeded:
                console.print(f"[red]Context window exceeded: {e}[/red]")
                # Reduce batch size for future calls
                if self._batch_size_cache and self._batch_size_cache > 50:
                    self._batch_size_cache = max(50, self._batch_size_cache // 2)
                    console.print(f"[yellow]Reduced batch size to {self._batch_size_cache}[/yellow]")
                with self._counter_lock:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        raise RuntimeError(
                            f"Circuit breaker: {self._consecutive_failures} consecutive LLM failures"
                        ) from e
                return "{}"
            is_transient = (
                (APITimeoutError is not None and isinstance(e, APITimeoutError))
                or (APIConnectionError is not None and isinstance(e, APIConnectionError))
                or (APIError is not None and isinstance(e, APIError))
                or "timed out" in err_text
                or "timeout" in err_text
            )
            if is_transient and retry_left > 0:
                return self._call_llm(prompt, is_retry=True, retry_left=retry_left - 1)
            console.print(f"[red]LLM call failed: {e}[/red]")
            with self._counter_lock:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._max_consecutive_failures:
                    raise RuntimeError(
                        f"Circuit breaker: {self._consecutive_failures} consecutive LLM failures"
                    ) from e
            return "{}"

    def _call_llm_json(self, prompt: str, max_retries: int = 3, is_retry: bool = False) -> dict:
        """Call LLM expecting a JSON dict response, with retry on format errors."""
        for attempt in range(max_retries):
            self._thread_local.truncated = False
            response = self._call_llm(prompt, is_retry=is_retry or attempt > 0)
            result = parse_json_from_response(response, default={})
            if isinstance(result, dict):
                return result
            # Don't retry if output was truncated (retrying won't help)
            if getattr(self._thread_local, "truncated", False):
                console.print("[yellow]Skipping retry: output was truncated[/yellow]")
                return {}
            console.print(
                f"[yellow]LLM returned {type(result).__name__} instead of dict "
                f"(attempt {attempt + 1}/{max_retries}), retrying...[/yellow]"
            )
        console.print("[red]All retries exhausted, returning empty dict[/red]")
        return {}

    def _format_skills_list(self, skills: list[dict]) -> str:
        """Format skills list for prompt."""
        lines = []
        for skill in self._sorted_skills(skills):
            desc = skill.get("description", "")
            if len(desc) > SKILL_DESCRIPTION_MAX_LENGTH:
                desc = desc[:SKILL_DESCRIPTION_MAX_LENGTH] + "..."
            lines.append(f"- {skill['id']}: {skill.get('name', skill['id'])}")
            if desc:
                lines.append(f"  {desc}")
        return "\n".join(lines)

    def _tree_to_dict(self, tree: TreeNode) -> dict:
        """Convert TreeNode to dict format for YAML (supports arbitrary depth)."""
        return self._node_to_dict(tree)

    def _tree_to_orchestrator_preset(self, tree_dict: dict) -> dict:
        nodes = self._flatten_capability_tree(tree_dict)
        nodes = self._rename_leaf_nodes(nodes)
        return {
            "tree_sketch": self._build_tree_sketch(nodes),
            "nodes": nodes,
        }

    def _flatten_capability_tree(self, tree: dict) -> list[dict]:
        nodes: list[dict] = []
        used_cids: set[str] = set()

        def walk_category(node: dict, parent_cid: str, top_category_id: str) -> None:
            branch_id = str(node.get("id") or node.get("name") or "category")
            branch_name = str(node.get("name") or branch_id)
            branch_description = str(node.get("description") or branch_name)
            branch_cid = self._unique_child_cid(parent_cid, self._cid_term(branch_id, fallback="Category"), used_cids)

            nodes.append(
                {
                    "cid": branch_cid,
                    "type": "branch",
                    "description": branch_description,
                    "keywords": self._extract_keywords(branch_id, branch_name, branch_description),
                    "examples": [],
                    "category": top_category_id,
                    "source_type": "capability_group",
                }
            )

            for child in list(node.get("children", []) or []):
                if isinstance(child, dict):
                    walk_category(child, branch_cid, top_category_id)

            for skill in list(node.get("skills", []) or []):
                if not isinstance(skill, dict):
                    continue
                skill_id = str(skill.get("id") or skill.get("name") or "skill").strip()
                skill_name = str(skill.get("name") or skill_id).strip()
                skill_description = str(skill.get("description") or skill_name).strip()
                leaf_cid = self._unique_child_cid(
                    branch_cid,
                    self._cid_term(skill_id or skill_name, fallback="Skill"),
                    used_cids,
                )
                nodes.append(
                    {
                        "cid": leaf_cid,
                        "type": "leaf",
                        "worker_id": skill_id,
                        "description": skill_description,
                        "keywords": self._extract_keywords(skill_id, skill_name, skill_description),
                        "examples": [],
                    }
                )

        root_children = tree.get("children", []) if str(tree.get("id", "")).strip().lower() == "root" else [tree]
        root_children = sorted(
            [item for item in root_children if isinstance(item, dict)],
            key=lambda item: str(item.get("id") or item.get("name") or ""),
        )
        for root_child in root_children:
            top_category_id = self._slug_term(
                str(root_child.get("id") or root_child.get("name") or "category"), fallback="category")
            walk_category(root_child, "", top_category_id)
        return nodes

    def _rename_leaf_nodes(self, nodes: list[dict]) -> list[dict]:
        """
        Final rename stage: re-name each leaf CID segment to a more descriptive term.

        Strategy:
        - Keep branch CIDs unchanged.
        - For each leaf, prefer skill display name, then worker_id, then old leaf segment.
        - Enforce global CID uniqueness while preserving parent path.
        """
        if not nodes:
            return nodes

        branch_cids = {
            str(item.get("cid", "")).strip()
            for item in nodes
            if str(item.get("type", "")).strip() == "branch" and str(item.get("cid", "")).strip()
        }
        used: set[str] = set(branch_cids)

        leaf_items = [
            item
            for item in nodes
            if str(item.get("type", "")).strip() == "leaf" and str(item.get("cid", "")).strip()
        ]
        # Stable order so results are deterministic across runs.
        leaf_items.sort(key=lambda item: str(item.get("cid", "")))

        renamed: dict[str, str] = {}
        for item in leaf_items:
            old_cid = str(item.get("cid", "")).strip()
            parent_cid = self._parent_cid(old_cid)
            old_term = old_cid.rsplit(".", 1)[-1] if old_cid else "Skill"
            preferred_cid_seed = self._compact_leaf_cid_seed(
                worker_id=str(item.get("worker_id") or "").strip(),
                display_name=str(item.get("name") or "").strip(),
                old_term=old_term,
            )
            segment = self._cid_term(preferred_cid_seed, fallback="Skill")
            new_cid = self._unique_child_cid(parent_cid, segment, used)
            renamed[old_cid] = new_cid

        updated: list[dict] = []
        for item in nodes:
            copied = dict(item)
            cid = str(copied.get("cid", "")).strip()
            if cid in renamed:
                copied["cid"] = renamed[cid]
            updated.append(copied)
        return updated

    @staticmethod
    def _compact_leaf_cid_seed(*, worker_id: str, display_name: str, old_term: str) -> str:
        """
        Generate a concise, semantic CID seed for leaf nodes.

        Design goals:
        - remove source/author/tooling noise (e.g., user prefixes, template words)
        - keep meaningful task tokens
        - remain deterministic and ASCII-safe for CID generation
        """
        # Prefer human-readable display name only when it has enough ASCII tokens.
        name_tokens = [t for t in re.split(r"[^A-Za-z0-9]+", display_name or "") if t]
        if len(name_tokens) >= 2:
            return " ".join(name_tokens)

        raw = worker_id or old_term or "Skill"
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", raw) if t]
        if not tokens:
            return "Skill"

        noise_prefix = {
            "claude", "code", "template", "templates", "skill", "skills", "plugin", "plugins",
            "repo", "github", "starter", "boilerplate", "awesome", "example", "examples",
        }
        noise_anywhere = {
            "template", "templates", "skill", "skills", "plugin", "plugins", "boilerplate",
        }

        compact = list(tokens)

        # Drop noisy leading terms and likely author/version prefixes.
        while len(compact) > 2:
            head = compact[0].lower()
            has_digit = any(ch.isdigit() for ch in head)
            if head in noise_prefix or has_digit:
                compact.pop(0)
                continue
            break

        # Drop generic words across the sequence while preserving at least 2 terms.
        filtered: list[str] = []
        for token in compact:
            if len(filtered) >= 2 and token.lower() in noise_anywhere:
                continue
            filtered.append(token)
        if len(filtered) >= 2:
            compact = filtered

        # Keep only the semantic tail when the slug remains too long.
        if len(compact) > 4:
            compact = compact[-4:]

        return " ".join(compact or tokens)

    @staticmethod
    def _cid_term(value: str, fallback: str = "Node") -> str:
        raw = str(value or "")
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", raw) if part]
        if not parts:
            parts = [fallback]
        # PascalCase segments make CID shorter/cleaner than kebab-case in prompts.
        token = "".join(part[:1].upper() + part[1:] for part in parts)
        if not token:
            token = fallback
        if not token[0].isalpha():
            token = "N" + token
        return token

    @staticmethod
    def _build_routing_policy(nodes: list[dict]) -> str:
        root_entries = sorted(
            [item for item in nodes if "." not in str(item.get("cid", ""))],
            key=lambda item: str(item.get("cid", "")),
        )
        lines = [
            "Route by descending the node tree one level at a time.",
            (
                "Treat a user request as potentially multi-step unless the latest observation "
                "already fully satisfies every explicit requirement."
            ),
            "Prefer leaves whose descriptions best match the next unmet sub-problem in the user request.",
            (
                "After a worker returns, check whether unmet requirements still remain; "
                "if they do, continue routing instead of finishing early."
            ),
            (
                "Do not jump to User.Final after a single worker call when the user asked "
                "for multiple actions, dependencies, or deliverables."
            ),
            (
                "Use worker observations as intermediate state: one skill may gather facts "
                "or create prerequisites for a later skill."
            ),
            "When multiple branches overlap, use the child descriptions as the local decision surface.",
            (
                "Choose User.Final only when the latest observation set is sufficient to "
                "answer the whole user request, not just one subtask."
            ),
        ]
        for item in root_entries:
            lines.append(f"If the request matches '{item['cid']}', continue under that branch.")
        return "\n".join(f"- {line}" for line in lines)

    def _build_tree_sketch(self, nodes: list[dict]) -> str:
        if not nodes:
            return ""

        by_cid = {str(item.get("cid", "")): item for item in nodes if item.get("cid")}
        children_by_parent: dict[str, list[dict]] = {}
        for node in nodes:
            cid = str(node.get("cid", "")).strip()
            if not cid:
                continue
            children_by_parent.setdefault(self._parent_cid(cid), []).append(node)

        leaf_count_cache: dict[str, int] = {}

        def descendant_leaf_count(cid: str) -> int:
            cached = leaf_count_cache.get(cid)
            if cached is not None:
                return cached
            node = by_cid.get(cid, {})
            if str(node.get("type", "")) != "branch":
                leaf_count_cache[cid] = 1
                return 1
            total = 0
            for child in children_by_parent.get(cid, []):
                child_cid = str(child.get("cid", ""))
                if child_cid:
                    total += descendant_leaf_count(child_cid)
            leaf_count_cache[cid] = total
            return total

        def branch_children(cid: str) -> list[dict]:
            return sorted(
                [item for item in children_by_parent.get(cid, []) if str(item.get("type", "")) == "branch"],
                key=lambda item: str(item.get("cid", "")),
            )

        def leaf_children(cid: str) -> list[dict]:
            return sorted(
                [item for item in children_by_parent.get(cid, []) if str(item.get("type", "")) != "branch"],
                key=lambda item: str(item.get("cid", "")),
            )

        lines: list[str] = [
            "Global Tree Sketch",
            "- Use this sketch only for global orientation across the whole tree.",
            "- When selecting a concrete node path, prefer the current local state over globally similar nodes.",
        ]

        def render_branch(cid: str, indent: int) -> None:
            node = by_cid.get(cid)
            if not node:
                return
            prefix = "  " * indent
            description = str(node.get("description", "")).strip() or "No summary"
            child_branches = branch_children(cid)
            child_leaves = leaf_children(cid)
            details = [description, f"descendant_leaves={descendant_leaf_count(cid)}"]
            if child_branches:
                details.append(
                    "child_branches="
                    + ", ".join(str(item.get("cid", "")).split(".")[-1]
                                for item in child_branches[:5] if item.get("cid"))
                )
            if child_leaves:
                details.append(
                    "representative_leaves="
                    + ", ".join(str(item.get("cid", "")).split(".")[-1] for item in child_leaves[:3] if item.get("cid"))
                )
            lines.append(f"{prefix}- {cid}: " + " | ".join(details))
            for child in child_branches:
                render_branch(str(child.get("cid", "")), indent + 1)

        root_branches = sorted(
            [item for item in children_by_parent.get("", []) if str(item.get("type", "")) == "branch"],
            key=lambda item: str(item.get("cid", "")),
        )
        for branch in root_branches:
            render_branch(str(branch.get("cid", "")), 0)
        return "\n".join(lines).strip()

    @staticmethod
    def _slug_term(value: str, fallback: str = "node") -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or ""))
        cleaned = cleaned.replace("_", "-").lower()
        cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
        if not cleaned:
            cleaned = fallback
        if not cleaned[0].isalpha():
            cleaned = f"n-{cleaned}"
        return cleaned

    @staticmethod
    def _join_cid(parent: str, child: str) -> str:
        return f"{parent}.{child}" if parent else child

    @staticmethod
    def _parent_cid(cid: str) -> str:
        if "." not in cid:
            return ""
        return cid.rsplit(".", 1)[0]

    def _unique_child_cid(self, parent_cid: str, segment: str, used: set[str]) -> str:
        base = self._join_cid(parent_cid, segment)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def _extract_keywords(*values: str, limit: int = 8) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            for token in re.findall(r"[A-Za-z0-9]+", str(value or "").lower()):
                if len(token) < 3 or token in _GENERIC_TERMS:
                    continue
                if token in seen:
                    continue
                seen.add(token)
                result.append(token)
                if len(result) >= limit:
                    return result
        return result

    def _node_to_dict(self, node: TreeNode) -> dict:
        """Recursively convert a node to dict."""
        result = {
            "id": node.id,
            "name": node.name,
            "description": node.description,
        }

        if node.children:
            result["children"] = [self._node_to_dict(child) for child in node.children]

        if node.skills:
            result["skills"] = [s.to_dict() for s in node.skills]

        return result

    def _write_yaml(self, tree_dict: dict) -> None:
        """Write tree to YAML file."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if yaml is not None:
            with open(self.output_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    tree_dict,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                    width=120,
                )
            return
        try:
            from ruamel.yaml import YAML
        except ModuleNotFoundError as exc:
            raise RuntimeError("A YAML writer is required. Install PyYAML (`yaml`) or `ruamel.yaml`.") from exc
        yml = YAML()
        yml.default_flow_style = False
        yml.allow_unicode = True
        with open(self.output_path, "w", encoding="utf-8") as f:
            yml.dump(tree_dict, f)

    def _print_tree(self, tree_dict: dict) -> None:
        """Print tree structure using rich (supports arbitrary depth)."""
        total_skills = self._count_skills_in_dict(tree_dict)
        rich_tree = RichTree(f"[bold]{tree_dict.get('name', 'Skill Tree')}[/bold] ({total_skills} skills)")

        for child in tree_dict.get("children", []):
            self._add_node_to_rich_tree(rich_tree, child)

        console.print(rich_tree)

    def _add_node_to_rich_tree(self, parent_branch, node_dict: dict) -> None:
        """Recursively add nodes to rich tree."""
        node_skills = self._count_skills_in_dict(node_dict)
        node_id = node_dict.get("id", "unknown")

        # Color based on depth (alternating)
        has_children = bool(node_dict.get("children"))
        if has_children:
            label = f"[yellow]{node_id}[/yellow] ({node_skills} skills)"
        else:
            label = f"[green]{node_id}[/green] ({node_skills} skills)"

        branch = parent_branch.add(label)

        # Add children recursively
        for child in node_dict.get("children", []):
            self._add_node_to_rich_tree(branch, child)

        # Add skills (leaf node)
        skills = node_dict.get("skills", [])
        for skill in skills[:3]:
            branch.add(f"[blue]{skill['id']}[/blue]")
        if len(skills) > 3:
            branch.add(f"[dim]... +{len(skills) - 3} more[/dim]")

    def _count_skills_in_dict(self, node_dict: dict) -> int:
        """Recursively count skills in a node dict."""
        count = len(node_dict.get("skills", []))
        for child in node_dict.get("children", []):
            count += self._count_skills_in_dict(child)
        return count


# Convenience function
def build_tree(
    skills_dir: Path | str | None = None,
    output_path: Path | str | None = None,
    config: DynamicTreeConfig | None = None,
    manager_config: TreeManagerConfig | None = None,
    **kwargs,
) -> dict:
    """Build capability tree."""
    client = kwargs.pop("client", None)
    model = kwargs.pop("model", None)
    api_key = kwargs.pop("api_key", None)
    base_url = kwargs.pop("base_url", None)
    llm_seed = kwargs.pop("llm_seed", None)
    max_workers = kwargs.pop("max_workers", None)
    verbose = bool(kwargs.pop("verbose", False))
    show_tree = bool(kwargs.pop("show_tree", True))
    generate_html = bool(kwargs.pop("generate_html", True))
    display_skills_dir = kwargs.pop("display_skills_dir", None)
    item_type = kwargs.pop("item_type", "skill")
    if kwargs:
        unknown = ", ".join(sorted(kwargs.keys()))
        raise TypeError(f"Unsupported build_tree keyword arguments: {unknown}")
    builder = TreeBuilder(
        skills_dir,
        output_path,
        config=config,
        manager_config=manager_config,
        client=client,
        model=model,
        api_key=api_key,
        base_url=base_url,
        llm_seed=llm_seed,
        max_workers=max_workers,
        display_skills_dir=display_skills_dir,
        item_type=item_type,
    )
    return builder.build(verbose=verbose, show_tree=show_tree, generate_html=generate_html)
