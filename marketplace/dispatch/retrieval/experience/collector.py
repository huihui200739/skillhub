from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any

from .embed import EmbeddingClient
from .bank import ExperienceBank
from .models import ExperienceItem, QuerySkillRecord
from .prompts import CLUSTER_NAME_PROMPT, PATTERN_EXTRACT_PROMPT

LOGGER = logging.getLogger(__name__)


class ExperienceCollector:
    """Records successful query-skill pairs and periodically clusters them
    into experience knowledge base entries.

    Flow:
      1. record_success() — stores raw query-skill pairs in a pending buffer
      2. flush() — clusters the buffer via embedding + LLM naming, writes to KB
      3. Can be called manually or on a schedule
    """

    def __init__(
        self,
        kb: ExperienceBank,
        embedding_client: EmbeddingClient,
        llm_client: Any | None = None,
        llm_model: str = "",
        *,
        min_hits_for_pattern: int = 2,
        pending_flush_threshold: int = 20,
    ) -> None:
        self._kb = kb
        self._embedder = embedding_client
        self._llm = llm_client
        self._llm_model = str(llm_model or "").strip()
        self._min_hits = int(min_hits_for_pattern)
        self._flush_threshold = int(pending_flush_threshold)
        # Pending buffer: raw records not yet clustered
        self._pending: list[QuerySkillRecord] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_success(self, query: str, skill_ids: list[str]) -> None:
        """Record a successful query-skill mapping.

        This adds to the pending buffer. Call flush() to cluster and persist.
        """
        record = QuerySkillRecord(query=query, skill_ids=skill_ids)
        with self._lock:
            self._pending.append(record)
            pending_count = len(self._pending)

        LOGGER.debug(
            "ExperienceCollector: recorded pending record query='%s' skills=%s (total pending=%d)",
            query, skill_ids, pending_count,
        )

        # Auto-flush if buffer is large enough (non-blocking)
        if pending_count >= self._flush_threshold:
            with self._lock:
                snapshot = list(self._pending)
                self._pending.clear()
            threading.Thread(
                target=self._flush_snapshot,
                args=(snapshot,),
                daemon=True,
            ).start()

    def flush(self) -> int:
        """Cluster pending records and merge into the KB.

        Returns the number of new experience items created.
        Blocks until complete — use for graceful shutdown only.
        """
        with self._lock:
            if not self._pending:
                return 0
            pending = list(self._pending)
            self._pending.clear()

        return self._flush_snapshot(pending)

    def _flush_snapshot(self, pending: list[QuerySkillRecord]) -> int:
        """Flush a snapshot of pending records. Safe to call from any thread."""
        if not pending:
            return 0

        # Step 1: group by skill_ids to reduce noise first
        by_skill = defaultdict(list)
        for r in pending:
            by_skill[tuple(sorted(r.skill_ids))].append(r)

        created = 0
        for skill_key, records in by_skill.items():
            created += self._cluster_and_merge(records, list(skill_key))

        LOGGER.info(
            "ExperienceCollector: flushed %d pending records, created %d experience items",
            len(pending), created,
        )
        return created

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cluster_and_merge(
        self,
        records: list[QuerySkillRecord],
        skill_ids: list[str],
    ) -> int:
        """Embed records, cluster by semantic similarity, name each cluster,
        and write to KB.

        Returns number of items created.
        """
        if len(records) < self._min_hits:
            # Too few records — put back into pending for later
            self._pending.extend(records)
            return 0

        # If there's only one record type, no need to cluster
        if len(records) < 3:
            # Merge directly into a single pattern
            pattern = self._extract_pattern(records[0].query)
            item = self._try_merge_into_existing(pattern, [r.query for r in records], skill_ids)
            if item:
                return 1
            return 0

        # Cluster by embedding
        queries = [r.query for r in records]
        embeddings = self._embedder.embed_batch(queries)

        cluster_labels = _faiss_cluster(embeddings, min_cluster_size=2)

        created = 0
        from collections import defaultdict as _dd
        clusters: dict[int, list[QuerySkillRecord]] = _dd(list)
        noise: list[QuerySkillRecord] = []
        for i, label in enumerate(cluster_labels):
            if label >= 0:
                clusters[label].append(records[i])
            else:
                noise.append(records[i])

        # Put noise back into pending
        self._pending.extend(noise)

        # Name each cluster and write to KB
        for label, cluster_records in clusters.items():
            if len(cluster_records) < self._min_hits:
                self._pending.extend(cluster_records)
                continue

            # Generate pattern name via LLM
            examples = "\n".join(f"- {r.query}" for r in cluster_records[:5])
            pattern = self._cluster_name(examples)

            example_texts = [r.query for r in cluster_records]
            item = self._try_merge_into_existing(pattern, example_texts, skill_ids)
            if item:
                created += 1

        return created

    def _try_merge_into_existing(
        self,
        pattern: str,
        query_examples: list[str],
        skill_ids: list[str],
    ) -> ExperienceItem | None:
        """Check if an experience with similar pattern and same skills already exists.
        If yes, increment its count. If no, create a new item.
        """
        # Build a temporary embedding from new examples for similarity comparison
        new_text = pattern + "\n" + "\n".join(query_examples)
        new_emb = self._embedder.embed(new_text)

        # Convert skill_ids to sorted tuple for comparison
        skill_key = tuple(sorted(skill_ids))

        # Prepare numpy arrays for batch computation
        import numpy as np

        # Filter items with same skill_ids and valid embeddings
        candidate_items = []
        candidate_embeddings = []

        for item in self._kb.items:
            if tuple(sorted(item.skill_ids)) != skill_key:
                continue
            if not item.embedding:
                continue
            candidate_items.append(item)
            candidate_embeddings.append(item.embedding)

        if not candidate_items:
            # No candidates with same skill_ids, create new item
            return self._create_new_item(pattern, query_examples, skill_ids)

        # Convert to numpy arrays for batch computation
        new_emb_np = np.asarray(new_emb, dtype=np.float32)
        candidate_emb_np = np.asarray(candidate_embeddings, dtype=np.float32)

        # Normalize vectors for cosine similarity
        new_norm = np.linalg.norm(new_emb_np)
        if new_norm == 0:
            # Invalid embedding, create new item
            return self._create_new_item(pattern, query_examples, skill_ids)

        new_emb_norm = new_emb_np / new_norm

        # Normalize candidate embeddings
        candidate_norms = np.linalg.norm(candidate_emb_np, axis=1, keepdims=True)
        candidate_norms[candidate_norms == 0] = 1.0  # Avoid division by zero
        candidate_emb_norm = candidate_emb_np / candidate_norms

        # Batch cosine similarity computation
        similarities = np.dot(candidate_emb_norm, new_emb_norm)

        # Find best match
        best_idx = np.argmax(similarities)
        best_sim = float(similarities[best_idx])
        best_item = candidate_items[best_idx]

        merge_threshold = 0.75
        if best_sim >= merge_threshold:
            # Merge: add examples and increment count
            for q in query_examples:
                if q not in best_item.query_examples:
                    best_item.query_examples.append(q)
            best_item.success_count += 1
            self._kb.persist()
            LOGGER.info(
                "ExperienceCollector: merged into existing item '%s' (sim=%.3f)",
                best_item.id, best_sim,
            )
            return best_item

        # Create new item
        return self._create_new_item(pattern, query_examples, skill_ids)

    def _create_new_item(
        self,
        pattern: str,
        query_examples: list[str],
        skill_ids: list[str],
    ) -> ExperienceItem:
        """Helper to create a new experience item."""
        item = self._kb.create_item(
            query_pattern=pattern,
            query_examples=query_examples,
            skill_ids=skill_ids,
        )
        self._kb.add(item)
        LOGGER.info("ExperienceCollector: created new item '%s' pattern='%s'", item.id, pattern)
        return item

    def _extract_pattern(self, query: str) -> str:
        """Use LLM to extract the intent category from a single query."""
        if self._llm and self._llm_model:
            try:
                resp = self._llm.complete(
                    model=self._llm_model,
                    messages=[{"role": "user", "content": PATTERN_EXTRACT_PROMPT.format(query=query)}],
                    max_tokens=32,
                )
                if resp and len(resp) > 0:
                    return resp[0].strip()
            except Exception as exc:
                LOGGER.debug("ExperienceCollector: LLM pattern extraction failed: %s", exc)

        # Fallback: return the query itself as the pattern
        return query

    def _cluster_name(self, examples_text: str) -> str:
        """Use LLM to name a cluster of queries."""
        if self._llm and self._llm_model:
            try:
                resp = self._llm.complete(
                    model=self._llm_model,
                    messages=[{"role": "user", "content": CLUSTER_NAME_PROMPT.format(examples=examples_text)}],
                    max_tokens=32,
                )
                if resp and len(resp) > 0:
                    return resp[0].strip()
            except Exception as exc:
                LOGGER.debug("ExperienceCollector: LLM cluster naming failed: %s", exc)

        # Fallback: use first example as the pattern
        first_line = examples_text.split("\n")[0].strip()
        if first_line.startswith("- "):
            return first_line[2:]
        return first_line


# ---------------------------------------------------------------------------
# FAISS-based semantic clustering
# ---------------------------------------------------------------------------

def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (numpy)."""
    import numpy as np
    vec_a = np.asarray(a, dtype=np.float32)
    vec_b = np.asarray(b, dtype=np.float32)
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


def _faiss_cluster(
    embeddings: list[list[float]],
    *,
    n_clusters: int | None = None,
    max_iterations: int = 50,
    min_cluster_size: int = 2,
) -> list[int]:
    """Cluster embeddings using FAISS K-Means with cosine distance.

    Returns list of cluster labels (-1 = noise / too-small cluster).
    Falls back to all-in-one cluster if FAISS is unavailable.
    """
    import numpy as np

    n = len(embeddings)
    if n == 0:
        return []
    if n < min_cluster_size:
        return [-1] * n

    try:
        import faiss
    except ImportError:
        LOGGER.debug("FAISS not available, falling back to single-cluster")
        return [0] * n

    # Normalize vectors for inner-product = cosine similarity
    arr = np.array(embeddings, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms

    # Auto-determine k: aim for clusters of 3-8 items
    if n_clusters is None:
        k = max(1, min(n // 3, n))
    else:
        k = max(1, min(n_clusters, n))

    # FAISS K-Means with cosine distance (via inner product on normalized vectors)
    dim = arr.shape[1]
    kmeans = faiss.Kmeans(
        dim,
        k,
        niter=max_iterations,
        verbose=False,
        gpu=False,
        spherical=True,  # enforces cosine similarity
        min_points_per_centroid=min_cluster_size,
        seed=42,
    )
    kmeans.train(arr)
    _, labels = kmeans.index.search(arr, 1)
    labels = labels.flatten().tolist()

    # Mark clusters smaller than min_cluster_size as noise (-1)
    from collections import Counter
    counts = Counter(labels)
    final = [-1 if counts[x] < min_cluster_size else x for x in labels]
    return final


__all__ = ["ExperienceCollector"]
