from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FinderItem:
    item_id: str
    payload: str
    label: str = ""
    description: str = ""


@dataclass(frozen=True)
class RetrieverChoice:
    choice_id: str
    payload: str
    description: str = ""


@dataclass(frozen=True)
class FinderNode:
    node_id: str
    label: str
    description: str = ""
    children: tuple["FinderNode", ...] = ()
    items: tuple[FinderItem, ...] = ()


__all__ = ["FinderItem", "FinderNode", "RetrieverChoice"]
