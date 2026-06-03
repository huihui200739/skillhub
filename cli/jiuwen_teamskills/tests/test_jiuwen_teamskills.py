# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli_core.plugin import plugin_validate
from cli_core.schemas import PluginListItem, PluginListResponse

from cli_core.schemas import PluginVersionDetail
from jiuwen_teamskills.main import main
from jiuwen_teamskills.parsers import build_swarmskill_parser


class SwarmskillCliTest(unittest.TestCase):
    def test_init_swarmskill_via_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "hub-demo", "--path", tmp])
            self.assertEqual(code, 0)
            root = Path(tmp) / "hub-demo"
            self.assertFalse((root / "plugin.yaml").exists())
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("kind: swarm-skill", text)
            self.assertIn("id: id_01", text)
            self.assertIn("id: id_02", text)

    def test_init_swarmskill_skill_md_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "hub-roles", "--path", tmp])
            self.assertEqual(code, 0)
            root = Path(tmp) / "hub-roles"
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            for rid in ("id_01", "id_02"):
                self.assertIn(f"id: {rid}", text, msg=rid)

    def test_validate_bad_flat_skill_reports_skill_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text(
                "---\nname: Bad_Name\ndescription: bad\n---\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertFalse(result.ok)
            self.assertTrue(any("SKILL.md frontmatter name" in err for err in result.errors))
            self.assertFalse(any("missing required entry: plugin.yaml" in err for err in result.errors))
            self.assertFalse(any("missing required entry: README.md" in err for err in result.errors))

    def test_validate_bad_nested_skill_reports_skill_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "demo-skill"
            nested.mkdir()
            (nested / "SKILL.md").write_text(
                "---\n"
                "name: other-skill\n"
                "description: bad\n"
                "kind: swarm-skill\n"
                "roles:\n"
                "  - id: id_01\n"
                "  - id: id_02\n"
                "---\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertFalse(result.ok)
            self.assertIn(
                "SKILL.md frontmatter name ('other-skill') must equal skill directory name ('demo-skill')",
                result.errors,
            )
            self.assertFalse(any("missing required entry: plugin.yaml" in err for err in result.errors))
            self.assertFalse(any("missing required entry: README.md" in err for err in result.errors))

    def test_publish_bad_nested_skill_reports_skill_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "demo-skill"
            nested.mkdir()
            (nested / "SKILL.md").write_text(
                "---\n"
                "name: other-skill\n"
                "description: bad\n"
                "kind: swarm-skill\n"
                "roles:\n"
                "  - id: id_01\n"
                "  - id: id_02\n"
                "---\n",
                encoding="utf-8",
            )
            with patch("cli_core.handlers.logger.error") as m_error:
                code = main([
                    "publish",
                    str(root),
                    "--version",
                    "0.0.1",
                    "--system-token",
                    "token",
                    "--market-url",
                    "http://127.0.0.1:8100",
                ])
            self.assertEqual(code, 1)
            self.assertTrue(m_error.called)
            self.assertIn(
                "SKILL.md frontmatter name ('other-skill') must equal skill directory name ('demo-skill')",
                str(m_error.call_args),
            )

    def test_validate_legacy_team_skill_kind_still_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text(
                "---\nname: hub-demo\ndescription: ok\nkind: team-skill\nroles:\n  - id: id_01\n  - id: id_02\n---\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertEqual(result.runtime_type, "swarmskill")

    def test_validate_swarm_skill_kind_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text(
                "---\nname: hub-demo\ndescription: ok\nkind: swarm-skill\nroles:\n  - id: id_01\n  - id: id_02\n---\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertEqual(result.runtime_type, "swarmskill")
            self.assertTrue(result.ok)

    def test_validate_plugin_yaml_legacy_teamskills_runtime_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plugin.yaml").write_text(
                "name: hub-demo\n"
                "version: 1.0.0\n"
                "display_name: Hub Demo\n"
                "description: ok\n"
                "runtime:\n"
                "  type: teamskills\n"
                "metadata:\n"
                "  author: tester\n"
                "  tags: []\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            (root / "SKILL.md").write_text(
                "---\nname: hub-demo\ndescription: ok\nkind: swarm-skill\nroles:\n  - id: id_01\n  - id: id_02\n---\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertEqual(result.runtime_type, "swarmskill")
            self.assertTrue(result.ok, msg=result.errors)

    def test_validate_plain_dir_still_reports_generic_plugin_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = plugin_validate(Path(tmp))
            self.assertFalse(result.ok)
            self.assertIn("missing required entry: plugin.yaml", result.errors)
            self.assertIn("missing required entry: README.md", result.errors)

    def test_init_accepts_explicit_swarmskill_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "hub-explicit", "--type", "swarmskill", "--path", tmp])
            self.assertEqual(code, 0)
            text = (Path(tmp) / "hub-explicit" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("kind: swarm-skill", text)

    def test_publish_accepts_id_flag(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["publish", "/tmp/fake", "--id", "sk-123", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.skill_command, "publish")
        self.assertEqual(args.plugin_id, "sk-123")
        self.assertTrue(getattr(args, "_swarmskill_cli", False))

    def test_publish_version_maps_to_plugin_version(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(
            [
                "publish",
                "/tmp/fake",
                "--version",
                "1.2.3",
                "--token",
                "t",
                "--market-url",
                "http://x",
            ]
        )
        self.assertEqual(args.plugin_version, "1.2.3")

    def test_publish_short_v_maps_to_plugin_version(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["publish", "/tmp/fake", "-v", "2.0.0", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.plugin_version, "2.0.0")

    def test_delete_uses_skill_id_positional(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["delete", "sk-9", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.skill_id, "sk-9")

    def test_delete_short_v_maps_to_version(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["delete", "sk-9", "-v", "1.0.0", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.version, "1.0.0")

    def test_search_type_only_skill_or_swarmskill(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        a = parser.parse_args(["search", "kw", "--type", "skill", "--market-url", "http://x"])
        self.assertEqual(a.plugin_type, "skill")
        b = parser.parse_args(["search", "", "--type", "swarmskill", "--market-url", "http://x"])
        self.assertEqual(b.plugin_type, "swarmskill")
        c = parser.parse_args(["search", "legacy", "--type", "teamskills", "--market-url", "http://x"])
        self.assertEqual(c.plugin_type, "swarmskill")
        d = parser.parse_args(["search", "x", "--market-url", "http://x"])
        self.assertEqual(d.plugin_type, "skill,swarmskill")
        e = parser.parse_args(["search", "x", "--type", "skill,swarmskill", "--market-url", "http://x"])
        self.assertEqual(e.plugin_type, "skill,swarmskill")
        f = parser.parse_args(["search", "x", "--type", "teamskills,skill", "--market-url", "http://x"])
        self.assertEqual(f.plugin_type, "skill,swarmskill")

    def test_init_accepts_legacy_teamskills_type_value(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["init", "demo", "--type", "teamskills"])
        self.assertEqual(args.plugin_type, "swarmskill")

    def test_info_uses_explicit_market_url(self) -> None:
        with patch("cli_core.handlers.resolve_skill_like_asset_for_cli") as m_skill_like:
            m_skill_like.return_value = PluginListItem(asset_id="asset-1", name="n", plugin_type="skill")
            with patch("cli_core.handlers.plugin_info") as m_info:
                m_info.return_value = PluginVersionDetail(asset_id="a1", name="n", version="1.0.0")
                code = main(["info", "asset-1", "--version", "1.0.0", "--market-url", "http://x"])
                self.assertEqual(code, 0)
                m_info.assert_called_once()
                self.assertEqual(m_info.call_args[0][0], "http://x")

    def test_info_requires_market_url_when_env_missing(self) -> None:
        with patch("cli_core.handlers.plugin_info") as m_info:
            with patch.dict("os.environ", {}, clear=True):
                code = main(["info", "asset-1", "--version", "1.0.0"])
            self.assertEqual(code, 1)
            m_info.assert_not_called()

    def test_legacy_init_teamskills_subcommand_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init-teamskills", "legacy-hub", "--path", tmp])
            self.assertEqual(code, 0)
            root = Path(tmp) / "legacy-hub"
            self.assertTrue((root / "SKILL.md").is_file())

    def test_legacy_search_teamskills_subcommand_sets_marker(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["search-teamskills", "kw", "--market-url", "http://x", "--type", "swarmskill"])
        self.assertEqual(args.skill_command, "search")
        self.assertEqual(args.plugin_type, "swarmskill")
        self.assertTrue(getattr(args, "_teamskills_cli", False))
        self.assertTrue(getattr(args, "_swarmskill_cli", False))

    def test_legacy_search_teamskills_accepts_multi_type_filter(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["search-teamskills", "kw", "--market-url", "http://x", "--type", "teamskills,skill"])
        self.assertEqual(args.skill_command, "search")
        self.assertEqual(args.plugin_type, "skill,swarmskill")

    def test_legacy_search_teamskills_defaults_to_skill_like_types(self) -> None:
        parser = build_swarmskill_parser("jiuwen-teamskills")
        args = parser.parse_args(["search-teamskills", "kw", "--market-url", "http://x"])
        self.assertEqual(args.plugin_type, "skill,swarmskill")

    def test_info_rejects_non_skill_like_asset(self) -> None:
        with patch("cli_core.handlers.resolve_skill_like_asset_for_cli") as m_skill_like:
            m_skill_like.side_effect = ValueError("not a skill-like asset")
            with patch("cli_core.handlers.plugin_info") as m_info:
                code = main(["info", "asset-1", "--market-url", "http://x"])
            self.assertEqual(code, 1)
            m_info.assert_not_called()

    def test_install_rejects_non_skill_like_asset(self) -> None:
        with patch("cli_core.handlers.resolve_skill_like_asset_for_cli") as m_skill_like:
            m_skill_like.side_effect = ValueError("not a skill-like asset")
            with patch("cli_core.handlers.plugin_install_download") as m_download:
                code = main(["install", "asset-1", "--market-url", "http://x"])
            self.assertEqual(code, 1)
            m_download.assert_not_called()

    def test_delete_rejects_non_skill_like_asset(self) -> None:
        with patch("cli_core.handlers.resolve_skill_like_asset_for_cli") as m_skill_like:
            m_skill_like.side_effect = ValueError("not a skill-like asset")
            with patch("cli_core.handlers.plugin_delete") as m_delete:
                code = main(["delete", "asset-1", "--token", "t", "--market-url", "http://x"])
            self.assertEqual(code, 1)
            m_delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
