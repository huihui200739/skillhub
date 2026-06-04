from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

DISPATCH_ROOT = Path(__file__).resolve().parents[1]
if str(DISPATCH_ROOT) not in sys.path:
    sys.path.insert(0, str(DISPATCH_ROOT))

from indexing.tree.builder import TreeBuilder
from indexing.tree.schema import Skill, TreeNode


def _skill(skill_id: str, *, name: str | None = None, description: str = "") -> Skill:
    return Skill(id=skill_id, name=name or skill_id, description=description or skill_id, path="test")


class TreeBuilderPostprocessTests(unittest.TestCase):
    def _builder(self) -> TreeBuilder:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        builder = TreeBuilder(
            skills_dir=Path(tmp_dir.name),
            model="test-tree-model",
            client=object(),  # type: ignore[arg-type]
        )
        builder._postprocess_min_skills = 2
        builder._equiv_allow_singleton_groups = False
        return builder

    def test_rebalance_child_assignments_moves_skills_between_leaf_siblings(self) -> None:
        builder = self._builder()
        data_leaf = TreeNode(
            id="data-processing",
            name="Data Processing",
            description="Structured analysis.",
            skills=[_skill("sql-reporting"), _skill("web-crawler")],
        )
        automation_leaf = TreeNode(
            id="automation",
            name="Automation",
            description="Workflow automation.",
            skills=[_skill("browser-automation"), _skill("workflow-builder")],
        )
        parent = TreeNode(id="root-branch", name="Root Branch", children=[data_leaf, automation_leaf])
        builder._classify_skills = lambda skills, groups, verbose=False: {
            "sql-reporting": "data-processing",
            "web-crawler": "automation",
            "browser-automation": "automation",
            "workflow-builder": "automation",
        }
        builder._validate_and_recover = lambda skills, groups, assignments, verbose=False: assignments

        moved = builder._rebalance_child_assignments(parent)

        self.assertEqual(moved, 1)
        self.assertEqual([skill.id for skill in data_leaf.skills], ["sql-reporting"])
        self.assertEqual(
            sorted(skill.id for skill in automation_leaf.skills),
            ["browser-automation", "web-crawler", "workflow-builder"],
        )

    def test_rebalance_child_assignments_routes_into_existing_subtree(self) -> None:
        builder = self._builder()
        analysis_leaf = TreeNode(
            id="analysis",
            name="Analysis",
            description="Analysis tools.",
            skills=[_skill("sql-reporting"), _skill("web-crawler")],
        )
        browser_leaf = TreeNode(id="browser-automation", name="Browser Automation", skills=[_skill("playwright-local")])
        workflow_leaf = TreeNode(id="workflow-automation", name="Workflow Automation", skills=[_skill("cron-runner")])
        automation_branch = TreeNode(id="automation", name="Automation", children=[browser_leaf, workflow_leaf])
        parent = TreeNode(id="root-branch", name="Root Branch", children=[analysis_leaf, automation_branch])
        builder._classify_skills = lambda skills, groups, verbose=False: {
            "sql-reporting": "analysis",
            "web-crawler": "automation",
            "playwright-local": "automation",
            "cron-runner": "automation",
        }
        builder._validate_and_recover = lambda skills, groups, assignments, verbose=False: assignments
        builder._classify_skills_single = (
            lambda skills, groups, verbose=False, is_retry=False: {
                skills[0]["id"]: "browser-automation" if skills[0]["id"] == "web-crawler" else next(iter(groups.keys()))
            }
        )

        moved = builder._rebalance_child_assignments(parent)

        self.assertEqual(moved, 1)
        self.assertEqual([skill.id for skill in analysis_leaf.skills], ["sql-reporting"])
        self.assertEqual(sorted(skill.id for skill in browser_leaf.skills), ["playwright-local", "web-crawler"])
        self.assertEqual([skill.id for skill in workflow_leaf.skills], ["cron-runner"])

    def test_repair_small_leaf_children_merges_singleton_group(self) -> None:
        builder = self._builder()
        singleton_leaf = TreeNode(id="singleton", name="Singleton", skills=[_skill("web-crawler")])
        stable_leaf = TreeNode(
            id="automation",
            name="Automation",
            skills=[_skill("browser-automation"), _skill("workflow-builder")],
        )
        data_leaf = TreeNode(
            id="data-processing",
            name="Data Processing",
            skills=[_skill("sql-reporting"), _skill("table-cleanup")],
        )
        parent = TreeNode(id="root-branch", name="Root Branch", children=[singleton_leaf, stable_leaf, data_leaf])
        builder._classify_skills_single = lambda skills, groups, verbose=False, is_retry=False: {"web-crawler": "automation"}

        reassigned = builder._repair_small_leaf_children(parent)

        self.assertEqual(reassigned, 1)
        self.assertEqual(sorted(child.id for child in parent.children), ["automation", "data-processing"])
        self.assertEqual(
            sorted(skill.id for skill in stable_leaf.skills),
            ["browser-automation", "web-crawler", "workflow-builder"],
        )

    def test_equivalence_group_id_prefers_semantic_name_and_skips_root_children(self) -> None:
        builder = TreeBuilder.__new__(TreeBuilder)
        group_id = builder._build_equivalence_group_id(
            group_id="G1",
            group_name="Academic Literature Search",
            fallback="search-research-equiv-1",
        )
        called = {"value": False}

        def fail_if_called(parent_node, second_leaf_node, verbose=False):
            called["value"] = True
            raise AssertionError("root-level equivalence regrouping should be skipped")

        builder._split_second_leaf_node_into_equiv_groups = fail_if_called
        root = TreeNode(
            id="root",
            name="Root",
            children=[
                TreeNode(
                    id="search-research",
                    name="Search & Research",
                    children=[
                        TreeNode(id="left-leaf", name="Left Leaf", skills=[]),
                        TreeNode(id="right-leaf", name="Right Leaf", skills=[]),
                    ],
                )
            ],
        )

        builder._normalize_to_equivalence_groups(root)

        self.assertEqual(group_id, "academic-literature-search")
        self.assertFalse(called["value"])
        self.assertEqual([node.id for node in root.children], ["search-research"])


if __name__ == "__main__":
    unittest.main()
