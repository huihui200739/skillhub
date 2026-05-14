# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import BaseScanner, ScannedItem, console
from .common import clean_first_paragraph, parse_frontmatter


class SkillScanner(BaseScanner):
    item_type = "skill"

    @classmethod
    def _plugin_metadata_candidates(cls, path: Path) -> tuple[Path, ...]:
        candidates: list[Path] = []
        for parent in (path / ".codex-plugin", path, path.parent):
            for filename in ("plugin.yaml", "plugin.yml", "plugin.json"):
                candidates.append(parent / filename)
        return tuple(candidates)

    def __init__(self, items_dir: Path | str, *, display_items_dir: Path | str | None = None) -> None:
        super().__init__(items_dir, display_items_dir=display_items_dir)
        self._metadata: dict[str, dict[str, object]] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        metadata_path = self.items_dir / "skills.json"
        if not metadata_path.exists():
            return
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            console.print(f"[yellow]Warning: Failed to load skills.json: {exc}[/yellow]")
            return
        for item in payload.get("skills", []):
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            self._metadata[item_id] = {
                "github_url": item.get("github_url", ""),
                "stars": item.get("stars", 0),
                "is_official": item.get("is_official", False),
                "author": item.get("author", ""),
                "display_name": item.get("display_name", ""),
                "short_desc": item.get("short_desc", ""),
                "detail_desc": item.get("detail_desc", ""),
            }

    @classmethod
    def detect_item_root(cls, path: Path) -> Path | None:
        for filename in ("SKILL.md", "skill.md", "Skill.md"):
            candidate = path / filename
            if candidate.exists():
                return path
        return None

    def scan_item_dir(self, item_dir: Path) -> ScannedItem | None:
        item_root = self.detect_item_root(item_dir)
        if item_root is None:
            return None

        skill_file = None
        for name in ("SKILL.md", "skill.md", "Skill.md"):
            candidate = item_root / name
            if candidate.exists():
                skill_file = candidate
                break
        if skill_file is None:
            return None
        try:
            content = skill_file.read_text(encoding="utf-8")
        except Exception as exc:
            console.print(f"[yellow]Failed to read {skill_file}: {exc}[/yellow]")
            return None

        frontmatter, body = parse_frontmatter(content)
        item_id = item_root.name
        meta = self._metadata.get(item_id, {})
        name = str(frontmatter.get("name") or item_root.name).strip() or item_root.name
        description = str(frontmatter.get("description") or "").strip()
        if not description:
            description = clean_first_paragraph(body)
        market_display_name = str(meta.get("display_name") or "").strip()
        market_short_desc = str(meta.get("short_desc") or "").strip()
        market_detail_desc = str(meta.get("detail_desc") or "").strip()
        plugin_display_name = self._load_plugin_display_name(item_root) or market_display_name
        if market_short_desc:
            description = "\n".join(part for part in (market_short_desc, description) if part)

        return ScannedItem(
            id=item_id,
            name=name,
            description=description,
            item_path=str(skill_file.resolve()),
            content=body.strip(),
            plugin_display_name=plugin_display_name,
            market_display_name=market_display_name,
            market_short_desc=market_short_desc,
            market_detail_desc=market_detail_desc,
            github_url=str(meta.get("github_url") or ""),
            stars=int(meta.get("stars") or 0),
            is_official=bool(meta.get("is_official")),
            author=str(meta.get("author") or ""),
        )

    @classmethod
    def _load_plugin_display_name(cls, item_root: Path) -> str:
        plugin_file = next(
            (candidate for candidate in cls._plugin_metadata_candidates(item_root) if candidate.exists()),
            None,
        )
        if plugin_file is None:
            return ""
        payload = cls._load_plugin_payload(plugin_file)
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("display_name") or "").strip()

    @staticmethod
    def _load_plugin_payload(plugin_file: Path) -> dict[str, Any] | None:
        try:
            if plugin_file.suffix.lower() == ".json":
                payload = json.loads(plugin_file.read_text(encoding="utf-8"))
            else:
                text = plugin_file.read_text(encoding="utf-8")
                try:
                    import yaml  # type: ignore
                except ModuleNotFoundError:
                    payload = SkillScanner._parse_simple_yaml_payload(text)
                else:
                    payload = yaml.safe_load(text) or {}
        except Exception as exc:
            console.print(f"[yellow]Failed to read {plugin_file}: {exc}[/yellow]")
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _parse_simple_yaml_payload(text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for raw_line in text.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            if raw_line[:1].isspace() or ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            clean_key = key.strip()
            clean_value = value.strip()
            if clean_key:
                payload[clean_key] = SkillScanner._parse_yaml_scalar(clean_value)
        return payload

    @staticmethod
    def _parse_yaml_scalar(value: str) -> str:
        text = str(value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            return text[1:-1]
        return text
