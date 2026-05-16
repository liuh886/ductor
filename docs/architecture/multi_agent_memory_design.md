# Multi-Agent Memory Design

## 1. Overview
Ductor's multi-agent memory model is dynamic-first. Shared and task-oriented hints are read at runtime, while durable agent history continues to live in agent-local Markdown plus the SQLite memory fragment backend. The important operational change is that `SHAREDMEMORY.md` is no longer treated as a file that should be mirrored into every agent's `MAINMEMORY.md`.

## 2. Memory Context Hierarchy

### L0/L1 - System Identity (cached)
`SOUL.md` carries the agent's role, constraints, and stable operating context. It is loaded through `workspace/loader.py` and fed into the provider as cached prompt material.

### L3/L4 - Dynamic Hints (on demand)
`TASKS.md` and `SHAREDMEMORY.md` are runtime hints, not bulk context dumps.

- `TASKS.md` provides routing/task-state hints.
- `SHAREDMEMORY.md` is read dynamically as shared alert context. Only a small recent tail is surfaced during prompt assembly, rather than the full file.

This keeps the shared channel cheap to read and avoids duplicating the same content into every sub-agent workspace.

### L5 - Durable Agent Memory
`MAINMEMORY.md` and SQLite `memory_fragments` remain the long-lived storage layers. Agent-local memory is still durable, inspectable, and eligible for extraction/sync into the database-backed fragment store.

## 3. Shared Memory Governance
`SHAREDMEMORY.md` is intended to be a narrow cross-agent alert channel:

- short-lived coordination notes
- environment or ownership changes that other agents must notice soon
- brief alerts that are useful even when only the tail is injected

It is not intended to be:

- a secret store
- a full transcript archive
- a large project notebook
- a replacement for agent-local `MAINMEMORY.md` or structured state

`SharedKnowledgeSync` now acts as a watcher/auditor rather than a replication engine. Its responsibilities are intentionally small:

- seed `SHAREDMEMORY.md` when missing
- watch for changes
- emit warnings when the file looks unsafe or misused

The watcher currently warns when the file grows beyond the intended alert-channel size or contains secret-like lines. These warnings are advisory to preserve backward compatibility, but they make unsafe usage visible instead of silently normalizing it.

## 4. Alignment with the Current Codebase
This design matches the repository's current runtime split:

- `workspace/loader.py` and prompt-building flows handle dynamic shared-memory reads.
- `runtime/state/*` continues to own durable extraction and SQLite-backed memory fragments.
- `multiagent/shared_knowledge.py` provides watcher-side governance only; it does not physically sync shared content into each agent's `MAINMEMORY.md`.

This avoids redundant I/O and reduces the risk of stale or conflicting shared-memory copies across agents.

## 5. Remaining Gaps
Two areas remain separate follow-up work:

- stronger semantic retrieval over historical memory fragments
- structured persistence of delegated-task failures and recovery context

Those improvements are orthogonal to shared-memory governance and do not require reintroducing physical file mirroring.
