from __future__ import annotations

from prompts import RETRIEVAL_PROTOCOLS_YAML, get_prompt


def build_retriever_system_prompt(*, tree_cid_hierarchy: str, top_k: int) -> str:
    resolved_top_k = max(1, int(top_k or 1))
    key = "topk_template" if resolved_top_k > 1 else "top1_template"
    template = get_prompt(RETRIEVAL_PROTOCOLS_YAML, key)
    return template.format(
        top_k=resolved_top_k,
        tree_cid_hierarchy=str(tree_cid_hierarchy or "").strip() or "(no candidates)",
    )


def build_retriever_catalog_prompt(*, choices, top_k: int) -> str:
    resolved_top_k = max(1, int(top_k or 1))
    lines = [
        "# Role",
        "- You are a retriever.",
        "- Rank candidate workers for the current user query.",
        "- Do not explain your reasoning.",
        "",
        "# Goal",
        f"- Select the best {resolved_top_k} workers from the candidate list.",
        "",
        "# Output Rules",
        f"- Output up to {resolved_top_k} lines.",
        "- Each line must contain exactly 1 worker id from the candidate list.",
        "- Do not output explanations, numbering, JSON, or Markdown.",
        "",
        "# Candidates",
    ]
    has_choice = False
    for choice in choices:
        choice_id = str(getattr(choice, "choice_id", "") or "").strip()
        if not choice_id:
            continue
        description = " ".join(str(getattr(choice, "description", "") or "").split())
        line = f"- {choice_id}"
        if description:
            line += f": {description}"
        lines.append(line)
        has_choice = True
    if not has_choice:
        lines.append("- (no candidates)")
    return "\n".join(lines)


__all__ = ["build_retriever_catalog_prompt", "build_retriever_system_prompt"]
