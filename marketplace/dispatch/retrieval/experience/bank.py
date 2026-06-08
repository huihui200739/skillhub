from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .embed import EmbeddingClient
from .models import ExperienceItem

LOGGER = logging.getLogger(__name__)


class ExperienceBank:
    """Persistent JSONL-based knowledge base for experience items.

    Storage format (one JSON object per line):
      {"id":"exp_0","query_pattern":"...","skill_ids":[...],...}

    All items are loaded into memory for fast embedding search.
    """

    def __init__(
        self,
        storage_path: str | Path,
        embedding_client: EmbeddingClient,
    ) -> None:
        self._path = Path(storage_path)
        self._embedder = embedding_client
        self._items: list[ExperienceItem] = []
        self._id_index: dict[str, ExperienceItem] = {}
        self._lock = threading.Lock()  # 添加线程锁
        self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @property
    def items(self) -> list[ExperienceItem]:
        return list(self._items)

    @property
    def count(self) -> int:
        return len(self._items)

    def add(self, item: ExperienceItem) -> None:
        """Add a new experience item to the KB and persist."""
        with self._lock:
            self._items.append(item)
            self._id_index[item.id] = item
        self.persist()  # persist已经有锁了

    def remove(self, item_id: str) -> bool:
        """Remove an item by id. Returns True if found and removed."""
        with self._lock:
            item = self._id_index.pop(item_id, None)
            if item is None:
                return False
            self._items = [i for i in self._items if i.id != item_id]
        self.persist()
        return True

    # ------------------------------------------------------------------
    # Embedding search
    # ------------------------------------------------------------------

    def search_by_embedding(
        self,
        query: str,
        top_k: int = 1,
        threshold: float = 0.80,
    ) -> list[tuple[float, ExperienceItem]]:
        """Search the KB by embedding similarity using numpy for efficiency.

        Returns a list of (similarity_score, item) sorted descending.
        Items below threshold are excluded.
        """
        query_emb = self._embedder.embed(query)

        # Prepare numpy arrays for batch computation
        import numpy as np

        # Filter items with valid embeddings
        valid_items = []
        valid_embeddings = []

        for item in self._items:
            if item.embedding:
                valid_items.append(item)
                valid_embeddings.append(item.embedding)

        if not valid_items:
            return []

        # Convert to numpy arrays
        query_emb_np = np.asarray(query_emb, dtype=np.float32)
        item_embs_np = np.asarray(valid_embeddings, dtype=np.float32)

        # Normalize for cosine similarity
        query_norm = np.linalg.norm(query_emb_np)
        if query_norm == 0:
            return []

        query_emb_norm = query_emb_np / query_norm

        # Normalize item embeddings
        item_norms = np.linalg.norm(item_embs_np, axis=1, keepdims=True)
        item_norms[item_norms == 0] = 1.0  # Avoid division by zero
        item_embs_norm = item_embs_np / item_norms

        # Batch cosine similarity computation
        similarities = np.dot(item_embs_norm, query_emb_norm)

        # Filter by threshold and get top-k
        scored = []
        for i, sim in enumerate(similarities):
            if sim >= threshold:
                scored.append((float(sim), valid_items[i]))

        # Sort by similarity (descending) and return top-k
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def search_with_skill_ids(
        self,
        query: str,
        top_k: int = 1,
        threshold: float = 0.80,
    ) -> list[str]:
        """Convenience: return just the skill_ids of the best match."""
        results = self.search_by_embedding(query, top_k=top_k, threshold=threshold)
        skills: list[str] = []
        for _sim, item in results:
            for sid in item.skill_ids:
                if sid not in skills:
                    skills.append(sid)
        return skills

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            LOGGER.info("ExperienceBank: no storage file at %s, starting empty", self._path)
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    item = ExperienceItem.from_dict(data)
                    self._items.append(item)
                    self._id_index[item.id] = item
            LOGGER.info("ExperienceBank: loaded %d items from %s", len(self._items), self._path)
        except Exception as exc:
            LOGGER.warning("ExperienceBank: failed to load %s: %s", self._path, exc)

    def persist(self) -> None:
        """Write all items to the JSONL file (simple overwrite)."""
        with self._lock:  # 添加锁保护
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".jsonl.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for item in self._items:
                        f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
                # Atomic rename
                if os.name == "nt":
                    # Windows: remove destination first
                    if self._path.exists():
                        try:
                            self._path.unlink()
                        except PermissionError:
                            # 如果文件被锁定，等待一下再重试
                            import time
                            time.sleep(0.1)
                            if self._path.exists():
                                self._path.unlink()
                os.replace(str(tmp_path), str(self._path))
            except Exception as exc:
                LOGGER.error("ExperienceBank: failed to persist to %s: %s", self._path, exc)
                raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def generate_id(self) -> str:
        return f"exp_{len(self._items):04d}"

    def create_item(
        self,
        query_pattern: str,
        query_examples: list[str],
        skill_ids: list[str],
        success_count: int = 1,
    ) -> ExperienceItem:
        """Create an ExperienceItem with auto-generated embedding and id."""
        # Embed the pattern + examples concatenated
        text_for_embedding = query_pattern + "\n" + "\n".join(query_examples)
        embedding = self._embedder.embed(text_for_embedding)

        return ExperienceItem(
            id=self.generate_id(),
            query_pattern=query_pattern,
            query_examples=query_examples,
            skill_ids=skill_ids,
            success_count=success_count,
            embedding=embedding,
            created_at=_now(),
            last_hit_at=_now(),
        )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors using numpy for efficiency."""
    import numpy as np
    vec_a = np.asarray(a, dtype=np.float32)
    vec_b = np.asarray(b, dtype=np.float32)
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


def _now() -> float:
    import time
    return time.time()


__all__ = ["ExperienceBank"]
