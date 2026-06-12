# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli_core.schemas import PluginVersionDetail
from jiuwen_teamskills.main import main
from jiuwen_teamskills.parsers import build_teamskills_parser


class TeamskillsCliTest(unittest.TestCase):
    def test_init_teamskills_via_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "hub-demo", "--path", tmp])
            self.assertEqual(code, 0)
            root = Path(tmp) / "hub-demo"
            self.assertFalse((root / "plugin.yaml").exists())
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("kind: team-skill", text)
            self.assertIn("id: id_01", text)
            self.assertIn("id: id_02", text)

    def test_init_teamskills_skill_md_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "hub-roles", "--path", tmp])
            self.assertEqual(code, 0)
            root = Path(tmp) / "hub-roles"
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            for rid in ("id_01", "id_02"):
                self.assertIn(f"id: {rid}", text, msg=rid)

    def test_publish_accepts_id_flag(self) -> None:
        parser = build_teamskills_parser("jiuwen-teamskills")
        args = parser.parse_args(["publish", "/tmp/fake", "--id", "sk-123", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.skill_command, "publish")
        self.assertEqual(args.plugin_id, "sk-123")
        self.assertTrue(getattr(args, "_teamskills_cli", False))

    def test_publish_version_maps_to_plugin_version(self) -> None:
        parser = build_teamskills_parser("jiuwen-teamskills")
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
        parser = build_teamskills_parser("jiuwen-teamskills")
        args = parser.parse_args(["publish", "/tmp/fake", "-v", "2.0.0", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.plugin_version, "2.0.0")

    def test_delete_uses_skill_id_positional(self) -> None:
        parser = build_teamskills_parser("jiuwen-teamskills")
        args = parser.parse_args(["delete", "sk-9", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.skill_id, "sk-9")

    def test_delete_short_v_maps_to_version(self) -> None:
        parser = build_teamskills_parser("jiuwen-teamskills")
        args = parser.parse_args(["delete", "sk-9", "-v", "1.0.0", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.version, "1.0.0")

    def test_search_type_only_skill_or_teamskills(self) -> None:
        parser = build_teamskills_parser("jiuwen-teamskills")
        a = parser.parse_args(["search", "kw", "--type", "skill", "--market-url", "http://x"])
        self.assertEqual(a.plugin_type, "skill")
        b = parser.parse_args(["search", "", "--type", "teamskills", "--market-url", "http://x"])
        self.assertEqual(b.plugin_type, "teamskills")
        c = parser.parse_args(["search", "x", "--market-url", "http://x"])
        self.assertIsNone(c.plugin_type)

    def test_info_uses_explicit_market_url(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
