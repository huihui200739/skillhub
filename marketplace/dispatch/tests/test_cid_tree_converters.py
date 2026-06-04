from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from models.cid import (
    CID,
    CIDTree,
    build_worker_cid_converters_from_yaml_file,
    build_worker_cid_converters_from_yaml_text,
    load_worker_cid_converter_functions_from_yaml_file,
)


class CIDTreeConvertersTests(unittest.TestCase):
    @staticmethod
    def _mock_yaml_module(payload: dict) -> SimpleNamespace:
        def safe_load(_text: str) -> dict:
            return payload

        return SimpleNamespace(safe_load=safe_load)

    def test_build_worker_cid_converters_from_yaml_text(self) -> None:
        preset_yaml = textwrap.dedent(
            """
            nodes:
              - cid: Tools
                type: branch
                description: Tool group
              - cid: Tools.Writer
                type: leaf
                worker_id: writer
                description: Write content
              - cid: Tools.Reader
                type: leaf
                worker_id: reader
                description: Read content
            """
        )
        payload = {
            "nodes": [
                {"cid": "Tools", "type": "branch", "description": "Tool group"},
                {"cid": "Tools.Writer", "type": "leaf", "worker_id": "writer", "description": "Write content"},
                {"cid": "Tools.Reader", "type": "leaf", "worker_id": "reader", "description": "Read content"},
            ]
        }

        with patch.object(CIDTree, "_require_yaml_module", return_value=self._mock_yaml_module(payload)):
            converters = build_worker_cid_converters_from_yaml_text(preset_yaml)

        self.assertEqual(converters.worker_id_to_cid("writer"), "Tools.Writer")
        self.assertEqual(converters.worker_id_to_cid("reader"), "Tools.Reader")
        self.assertEqual(converters.cid_to_worker_id("Tools.Writer"), "writer")
        self.assertEqual(converters.cid_to_worker_id(CID.from_str("Tools.Reader")), "reader")
        self.assertIsNone(converters.worker_id_to_cid("missing"))
        self.assertIsNone(converters.cid_to_worker_id("Tools.Missing"))

    def test_load_worker_cid_converter_functions_from_yaml_file(self) -> None:
        preset_yaml = "nodes:\n  - cid: Skills.Search\n    type: leaf\n    worker_id: web-search\n"
        payload = {
            "nodes": [
                {"cid": "Skills.Search", "type": "leaf", "worker_id": "web-search", "description": "Search the web"}
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "preset.yaml"
            path.write_text(preset_yaml, encoding="utf-8")
            with patch.object(CIDTree, "_require_yaml_module", return_value=self._mock_yaml_module(payload)):
                worker_id_to_cid, cid_to_worker_id = load_worker_cid_converter_functions_from_yaml_file(path)

        self.assertEqual(worker_id_to_cid("web-search"), "Skills.Search")
        self.assertEqual(cid_to_worker_id("Skills.Search"), "web-search")

    def test_duplicate_worker_id_raises(self) -> None:
        payload = {
            "nodes": [
                {"cid": "Skills.Search", "type": "leaf", "worker_id": "shared-worker"},
                {"cid": "Skills.Browse", "type": "leaf", "worker_id": "shared-worker"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "preset.yaml"
            path.write_text("nodes: []\n", encoding="utf-8")
            with patch.object(CIDTree, "_require_yaml_module", return_value=self._mock_yaml_module(payload)):
                with self.assertRaisesRegex(ValueError, "Duplicate worker_id 'shared-worker'"):
                    build_worker_cid_converters_from_yaml_file(path)


if __name__ == "__main__":
    unittest.main()
