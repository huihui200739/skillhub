from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

from indexing import BM25Index, EmbeddingIndex, load_bm25_index, load_embedding_index
from models.retrieval import FinderItem, FinderNode, RetrieverChoice
from shared.storage import is_s3_uri, materialize_s3_dir


@dataclass(frozen=True)
class CatalogRecord:
    choice_id: str
    payload: str
    name: str = ""
    description: str = ""
    retrieval_text: str = ""
    branch_path: tuple[str, ...] = ()
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedFinderIndex:
    index_dir: Path
    tree_root: FinderNode
    choices: tuple[RetrieverChoice, ...]
    catalog_records: tuple[CatalogRecord, ...]
    embedding_index: EmbeddingIndex | None = None
    bm25_index: BM25Index | None = None
    manifest: Dict[str, object] = field(default_factory=dict)


def load_finder_index(index_dir: str | Path) -> LoadedFinderIndex:
    base_dir = _materialize_index_dir(index_dir)
    manifest = _load_manifest(base_dir)
    catalog_records = tuple(load_catalog_records(base_dir / _artifact_path(manifest, "catalog", "catalog.jsonl")))
    tree_root = load_tree_root(
        base_dir /
        _artifact_path(
            manifest,
            "tree_index",
            "tree_index.yaml"),
        catalog_records=catalog_records)
    choices = tuple(
        RetrieverChoice(
            choice_id=record.choice_id,
            payload=record.payload,
            description=record.description or record.retrieval_text,
        )
        for record in catalog_records
    )
    embedding_index_path = base_dir / _artifact_path(manifest, "embedding_index", "embedding_index.json")
    bm25_index_path = base_dir / _artifact_path(manifest, "bm25_index", "bm25_index.json")
    embedding_index = load_embedding_index(embedding_index_path) if embedding_index_path.exists() else None
    bm25_index = load_bm25_index(bm25_index_path) if bm25_index_path.exists() else None
    return LoadedFinderIndex(
        index_dir=base_dir,
        tree_root=tree_root,
        choices=choices,
        catalog_records=catalog_records,
        embedding_index=embedding_index,
        bm25_index=bm25_index,
        manifest=manifest,
    )


def _materialize_index_dir(index_dir: str | Path) -> Path:
    raw = str(index_dir).strip()
    if is_s3_uri(raw):
        return materialize_s3_dir(raw, cache_namespace="finder-s3-index-cache")
    return Path(index_dir).resolve()


def load_catalog_records(path: str | Path) -> List[CatalogRecord]:
    records: List[CatalogRecord] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        choice_id = str(payload.get("worker_id") or payload.get("choice_id") or payload.get("skill_id") or "").strip()
        candidate_payload = str(payload.get("cid") or payload.get("payload") or "").strip()
        if not choice_id or not candidate_payload:
            continue
        records.append(
            CatalogRecord(
                choice_id=choice_id,
                payload=candidate_payload,
                name=str(payload.get("name") or choice_id).strip() or choice_id,
                description=str(payload.get("description") or "").strip(),
                retrieval_text=str(payload.get("retrieval_text") or "").strip(),
                branch_path=tuple(str(item).strip()
                                  for item in (payload.get("branch_path") or []) if str(item).strip()),
                metadata={
                    "skill_path": str(payload.get("skill_path") or "").strip(),
                    "category": str(payload.get("category") or "").strip(),
                },
            )
        )
    records.sort(key=lambda item: (item.payload, item.choice_id))
    return records


def load_tree_root(path: str | Path, *, catalog_records: Sequence[CatalogRecord]) -> FinderNode:
    payload = _load_yaml_like(Path(path).read_text(encoding="utf-8"))
    nodes = payload.get("nodes") or []
    record_by_payload = {record.payload: record for record in catalog_records}
    return _build_tree_from_nodes(nodes, record_by_payload=record_by_payload)


def _build_tree_from_nodes(nodes: Sequence[object], *, record_by_payload: Dict[str, CatalogRecord]) -> FinderNode:
    branch_specs: Dict[str, Dict[str, object]] = {}
    children_map: Dict[str, List[str]] = {}
    items_by_parent: Dict[str, List[FinderItem]] = {}

    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        cid = str(raw_node.get("cid") or "").strip()
        node_type = str(raw_node.get("type") or "").strip().lower()
        if not cid:
            continue
        parent_cid = cid.rsplit(".", 1)[0] if "." in cid else ""
        if node_type == "leaf":
            record = record_by_payload.get(cid)
            item_id = str(raw_node.get("worker_id") or (
                record.choice_id if record else "") or cid.rsplit(".", 1)[-1]).strip()
            label = str((record.name if record else "") or item_id or cid.rsplit(".", 1)[-1]).strip() or item_id or cid
            description = str(raw_node.get("description") or (record.description if record else "") or "").strip()
            items_by_parent.setdefault(parent_cid, []).append(
                FinderItem(
                    item_id=item_id,
                    payload=cid,
                    label=label,
                    description=description,
                )
            )
            continue
        branch_specs[cid] = raw_node
        children_map.setdefault(parent_cid, []).append(cid)

    def build_branch(cid: str, *, root: bool = False) -> FinderNode | None:
        child_branch_ids = sorted(children_map.get(cid, []))
        children = [child for child in (build_branch(child_cid) for child_cid in child_branch_ids) if child is not None]
        items = sorted(items_by_parent.get(cid, []), key=lambda item: (item.payload, item.item_id))
        if not root and not children and not items:
            return None
        spec = branch_specs.get(cid, {})
        label = "ROOT" if root else (cid.rsplit(".", 1)[-1] if cid else "ROOT")
        description = str(spec.get("description") or "").strip() if isinstance(spec, dict) else ""
        return FinderNode(
            node_id="ROOT" if root else cid,
            label=label,
            description=description,
            children=tuple(children),
            items=tuple(items),
        )

    root = build_branch("", root=True)
    return root or FinderNode(node_id="ROOT", label="ROOT")


def _load_manifest(index_dir: Path) -> Dict[str, object]:
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _artifact_path(manifest: Dict[str, object], key: str, fallback: str) -> str:
    artifacts = manifest.get("artifacts") or {}
    if isinstance(artifacts, dict):
        value = str(artifacts.get(key) or "").strip()
        if value:
            return value
    return fallback


def _load_yaml_like(text: str) -> Dict[str, object]:
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text) or {}
        if isinstance(payload, dict):
            return payload
    except ModuleNotFoundError:
        return _parse_simple_nodes_yaml(text)
    return _parse_simple_nodes_yaml(text)


def _parse_simple_nodes_yaml(text: str) -> Dict[str, object]:
    nodes: List[Dict[str, str]] = []
    current: Dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped == "nodes:":
            continue
        if stripped.startswith("- "):
            if current:
                nodes.append(current)
            current = {}
            stripped = stripped[2:]
        if current is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = value.strip()
    if current:
        nodes.append(current)
    return {"nodes": nodes}


__all__ = [
    "CatalogRecord",
    "LoadedFinderIndex",
    "load_catalog_records",
    "load_finder_index",
    "load_tree_root",
]
