"""Shared prompts for experience KB pattern extraction and clustering."""

from typing import Any, Optional

# Extract intent from a single query
PATTERN_EXTRACT_PROMPT = (
    "You are classifying user queries about skill/tool selection.\n"
    "Given a user query, extract the INTENT CATEGORY (5-15 Chinese characters)\n"
    "that describes what kind of skill the user needs.\n"
    "Output ONLY the category name, nothing else.\n\n"
    "Query: {query}\n"
)

# Name a cluster of queries
CLUSTER_NAME_PROMPT = (
    "以下用户查询实际上属于同一类意图。请用 5-15 个字概括这类意图。\n"
    "只输出类别名称，不要解释。\n\n"
    "{examples}\n"
)


def extract_cluster_name(
    openai_client: Any,
    model: str,
    query_examples: list[str],
) -> tuple[str, int]:
    """Use LLM to name a cluster of queries.

    Returns (pattern_name, total_tokens).
    Falls back to a simple label if LLM fails.
    """
    examples_text = "\n".join(f"- {q}" for q in query_examples[:5])
    prompt = CLUSTER_NAME_PROMPT.format(examples=examples_text)

    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=32,
            temperature=0,
        )
        total_tokens = 0
        usage = getattr(resp, "usage", None)
        if usage is not None:
            total_tokens = getattr(usage, "total_tokens", 0) or 0
        pattern = (resp.choices[0].message.content or "").strip()
        return pattern or f"{query_examples[0] if query_examples else 'unknown'} 相关查询", total_tokens
    except Exception:
        fallback = query_examples[0] if query_examples else "unknown"
        return f"{fallback} 相关查询", 0
