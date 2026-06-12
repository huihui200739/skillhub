# Indexing

## Purpose

`indexing/` owns offline progressive tree index construction.

Given skill/plugin material directories or pre-scanned JSONL, it builds the tree, catalog, and manifest artifacts consumed later by `retrieval/` and orchestration.

## Main Outputs

- `tree_index.yaml`
- `catalog.jsonl`
- `manifest.json`

## Main Components

### `indexing/tree/`

- scans skills
- builds the capability tree
- supports LLM-driven tree construction and fallback tree generation

### `indexing/catalog/`

- defines catalog records
- builds the leaf catalog used by online progressive retrieval

### `indexing/io/`

- reads and writes tree, catalog, and manifest artifacts

### `indexing/workflows/`

- coordinates full builds and incremental add/delete rebuilds
- writes only the progressive tree artifacts listed above

## Main Entry Point

- `indexing/workflows/index_builder.py`

Typical usage:

```python
from indexing.workflows.index_builder import IndexBuilder

IndexBuilder.build(
    item_paths=["/abs/path/to/skills"],
    output_dir="/abs/path/to/index",
)
```

## Incremental Tree Maintenance

`IndexBuilder.add(...)` and `IndexBuilder.delete(...)` reuse an existing tree when
the change set is small. The incremental workflow applies the requested patch,
checks only the affected branches, and rebuilds a local subtree when semantic
repair is needed.

The workflow is:

1. Load the base tree, catalog, and manifest.
2. Calculate `change_count / base_catalog_count`. If the ratio exceeds
   `incremental_max_change_ratio`, run a full build.
3. Apply the incremental patch:
   - add: choose a parent branch and append the new leaf;
   - delete: remove the target leaf and recursively prune empty branches.
4. Check the selected parent branches and their ancestors for capacity and
   balance problems.
5. Rebuild the outermost unhealthy subtree with the configured tree LLM.
6. Write the updated tree, catalog, and manifest.

### Add Placement

Add placement compares the new skill with every existing branch. Tokens come
from the skill's id, name, description, and content, and from each branch's cid
and description.

Branches are ranked primarily by token overlap. Branch depth is used only as a
tie-breaker. Placement confidence is the F1 score of:

- the fraction of skill tokens covered by the branch;
- the fraction of branch tokens covered by the skill.

The confidence margin is the difference between the best and second-best branch
confidence. Low confidence or a small margin forces the selected parent subtree
into the rebuild candidate set.

### Affected Branch Health

Health checks do not scan every branch. They inspect the add/delete parent
branches and their ancestors.

A checked branch becomes a rebuild candidate when:

- its direct leaf count exceeds `tree_branching_factor`; or
- the ratio between the largest and smallest direct child-branch descendant
  leaf counts exceeds `incremental_branch_imbalance_ratio`.

Nested candidates are reduced to their outermost branch so that a parent and
its descendant are not rebuilt twice.

### Local LLM Rebuild

Local rebuild uses the same LLM client, model, timeout, branching factor, and
maximum depth as a full tree build. The LLM groups only the leaves under the
selected branch. Oversized groups are recursively grouped until they satisfy
the branching factor or reach the maximum depth.

If the LLM is unavailable, returns invalid output, or cannot produce a valid
subtree, the workflow keeps the completed add/delete patch. It does not apply a
deterministic text-based regrouping fallback.

### Incremental Configuration

| Option | Default | Purpose |
| --- | ---: | --- |
| `incremental_max_change_ratio` | `0.25` | Escalate a large change set to a full build. |
| `incremental_min_add_confidence` | `0.18` | Minimum confidence for add parent selection. |
| `incremental_min_add_confidence_margin` | `0.04` | Minimum gap between the best and second-best branch. |
| `incremental_branch_imbalance_ratio` | `3.0` | Maximum allowed child-branch leaf-count ratio. |

## Dependency Boundary

`indexing/` does not depend on `retrieval.service` or orchestration runtime code. It produces artifacts; it does not execute online retrieval.
