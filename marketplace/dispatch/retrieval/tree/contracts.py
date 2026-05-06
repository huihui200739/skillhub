from __future__ import annotations

from typing import Protocol, Sequence

from models.retrieval import RetrieverCandidate, RetrieverTrace

from .types import (
    CurrentSubtree,
    ExpansionPlan,
    NodeSearchResult,
    PromptBundle,
    SearchCursor,
    SelectionProtocol,
    SelectionResult,
)


class CurrentSubtreeProvider(Protocol):
    def get_current_subtree(self, *, cursor: SearchCursor) -> CurrentSubtree: ...


class SubtreeRenderer(Protocol):
    def render_subtree(
        self,
        *,
        subtree: CurrentSubtree,
        query_messages: Sequence[dict[str, str]],
        protocol: SelectionProtocol,
    ) -> PromptBundle: ...


class TopKSelector(Protocol):
    def build_protocol(self, *, subtree: CurrentSubtree) -> SelectionProtocol: ...

    def select_topk(
        self,
        *,
        model: str,
        cursor: SearchCursor,
        query_messages: Sequence[dict[str, str]],
        subtree: CurrentSubtree,
        prompt: PromptBundle,
        trace: RetrieverTrace,
    ) -> SelectionResult: ...


class TargetExpander(Protocol):
    def expand_selected_targets(
        self,
        *,
        cursor: SearchCursor,
        selected_targets: Sequence,
    ) -> ExpansionPlan: ...


class BranchReducer(Protocol):
    def reduce_branch_results(
        self,
        *,
        cursor: SearchCursor,
        local_leaves: Sequence[RetrieverCandidate],
        child_results: Sequence[NodeSearchResult],
    ) -> NodeSearchResult: ...
