# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Minimal prompt bank for Demo's tree indexer."""

GROUP_DISCOVERY_PROMPT = """Capability tree planning pass.

Scope note:
{context_section}

Candidate skills:
{skills_list}

Return {min_groups}-{max_groups} proposed groups only. Do not place skills into groups yet.

Design guidance:
- optimize for retrieval usefulness rather than implementation taxonomy
- keep groups distinct enough that a router can tell them apart
- prefer names that remain readable as tree labels
- ids should be lowercase and hyphenated

Respond as JSON:
{{
  "groups": {{
    "group-id": {{
      "name": "Short readable label",
      "description": "What belongs here and when a request should route here."
    }}
  }}
}}
"""

SKILL_ASSIGNMENT_PROMPT = """Routing pass for an existing tree layer.

Available groups:
{groups_list}

Skills awaiting placement:
{skills_list}

Rules:
- every skill must appear once
- only use one of the listed group ids
- choose the best primary fit for retrieval
- if a skill spans multiple groups, prefer the broadest correct home

Respond as JSON:
{{
  "assignments": {{
    "skill-id-1": "group-id",
    "skill-id-2": "group-id"
  }}
}}
"""

NODE_LABEL_REWRITE_PROMPT = """A tree node needs a cleaner label after regrouping.

Current node:
- id: {node_id}
- name: {node_name}
- description: {node_description}

Current children summary:
{children_summary}

Return a replacement name and description that better summarize the children now under this node.
Avoid mentioning repair passes or internal mechanics.

Respond as JSON:
{{
  "name": "Updated label",
  "description": "Updated node summary"
}}
"""

GROUP_MERGE_PROMPT = """Canonicalization pass across several discovery runs.

Candidate group definitions:
{all_groups}

Produce one merged set of canonical groups.
The final count must stay between {min_groups} and {max_groups}.
Merge synonyms where possible and keep labels stable enough for reuse in later indexing runs.

Respond as JSON:
{{
  "canonical_groups": {{
    "canonical-id": {{
      "name": "Canonical label",
      "description": "Canonical summary"
    }}
  }},
  "mapping": {{
    "source-group-id": "canonical-id"
  }}
}}
"""

EQUIVALENCE_GROUPING_PROMPT = """Equivalence regrouping pass for sibling leaves.

Parent:
- id: {parent_id}
- name: {parent_name}
- description: {parent_description}

Leaves:
{leaf_nodes}

Group leaf ids that are near substitutes during retrieval.
Partition all provided leaves into between 1 and {max_groups} equivalence groups.

Respond as JSON:
{{
  "groups": {{
    "group-id": {{
      "name": "Equivalence label",
      "description": "What these leaves have in common for routing.",
      "leaf_ids": ["leaf-a", "leaf-b"]
    }}
  }}
}}
"""
