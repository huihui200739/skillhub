# Repository Architecture

## Overview

The repository is built around a two-stage system:

1. `indexing/` builds structured offline artifacts from skill directories
2. `retrieval/` and `orchestration/` consume those artifacts online to retrieve and execute the right capability

The important architectural split is:

- offline organization of capabilities
- online routing and execution over that organized capability space

There is also an explicit packaging split:

- `indexing/`, `retrieval/`, `orchestration/`, `models/`, `shared/`, and `serving/` form the SDK codebase
- `demo/` and `training/` are consumers of that SDK
- `data/`, `tests/`, and `scripts/` support local development and are not SDK runtime dependencies

## Main Flow

### 1. Offline indexing

Input:

- skill directories
- skill metadata
- optional LLM support for tree construction

Output:

- `tree_index.yaml`
- `catalog.jsonl`
- `embedding_records.jsonl`
- `embedding_index.json`
- `bm25_index.json`
- `manifest.json`

Ownership:

- [indexing/tree](/home/doujzc/codes/Demo/indexing/tree)
- [indexing/catalog](/home/doujzc/codes/Demo/indexing/catalog)
- [indexing/embedding](/home/doujzc/codes/Demo/indexing/embedding)
- [indexing/bm25](/home/doujzc/codes/Demo/indexing/bm25)
- [indexing/workflows](/home/doujzc/codes/Demo/indexing/workflows)

### 2. Online retrieval

Input:

- a user query
- a loaded offline index
- optional LLM
- optional embedding model

Execution route:

1. progressive LLM tree search
2. embedding full-top-k backfill
3. BM25 full-top-k backfill
4. ordered dedupe merge

Ownership:

- [retrieval/tree](/home/doujzc/codes/Demo/retrieval/tree)
- [retrieval/semantic](/home/doujzc/codes/Demo/retrieval/semantic)
- [retrieval/lexical](/home/doujzc/codes/Demo/retrieval/lexical)
- [retrieval/merge](/home/doujzc/codes/Demo/retrieval/merge)
- [retrieval/service](/home/doujzc/codes/Demo/retrieval/service)

### 3. Orchestration

Input:

- retrieval results
- CID tree runtime
- user conversation state

Responsibilities:

- build runtime state
- route turns
- call retrieval when needed
- dispatch leaf nodes
- return final user-facing results

Ownership:

- [orchestration/engine](/home/doujzc/codes/Demo/orchestration/engine)
- [orchestration/routing](/home/doujzc/codes/Demo/orchestration/routing)
- [orchestration/runtime](/home/doujzc/codes/Demo/orchestration/runtime)
- [orchestration/retrieval_adapter](/home/doujzc/codes/Demo/orchestration/retrieval_adapter)

## Shared Layers

### `models/`

Shared contracts only:

- CID tree objects
- retrieval tree objects
- index record objects
- trace/result objects

### `shared/`

Generic helpers only:

- optional dependency fallbacks such as [shared/rich_compat.py](/home/doujzc/codes/Demo/shared/rich_compat.py)

## Current Package Roles

- [indexing](/home/doujzc/codes/Demo/indexing): offline build
- [retrieval](/home/doujzc/codes/Demo/retrieval): online retrieval
- [orchestration](/home/doujzc/codes/Demo/orchestration): runtime orchestration
- [demo](/home/doujzc/codes/Demo/demo): runnable examples that import and use the SDK
- [training](/home/doujzc/codes/Demo/training): training/eval built on top of the SDK
- [models](/home/doujzc/codes/Demo/models): shared contracts
- [shared](/home/doujzc/codes/Demo/shared): shared utilities
- [data](/home/doujzc/codes/Demo/data): local assets and generated artifacts, not imported by core packages
- [tests](/home/doujzc/codes/Demo/tests): verification only
- [scripts](/home/doujzc/codes/Demo/scripts): local tooling only

## Final State

The refactor is complete:

- no legacy runtime package roots remain
- no compatibility shells remain
- canonical packages own all live implementation code
- documentation describes the current architecture, not a transition state
