# Memory System Research

## Purpose

This note captures the current ductor memory architecture and the next design direction for
agent self-learning. It focuses on memory as an operational capability, not just prompt context.

## Current Architecture

ductor currently has three memory layers:

- `MAINMEMORY.md`: agent-local, human-readable durable memory.
- `SHAREDMEMORY.md`: short cross-agent alert channel.
- SQLite `memory_fragments`: structured fragments extracted from Markdown memory files.

The current runtime is dynamic-first:

- `workspace/loader.py` reads memory for prompt assembly.
- `runtime/memory/extractor.py` deterministically splits Markdown into fragments.
- `MemoryFragmentRepository` persists fragments and supports scope/agent queries.
- `SharedKnowledgeSync` watches shared memory and warns about unsafe usage.
- Message hooks periodically remind the agent to update `MAINMEMORY.md`.

This is a good foundation for durable context, but it is not yet a complete self-learning loop.

## What Works

- Memory remains inspectable by humans through Markdown.
- SQLite gives the system a structured retrieval and governance layer.
- Shared memory is intentionally narrow, reducing cross-agent drift.
- Fragment extraction is deterministic, so memory sync is reproducible.
- Existing tests cover extraction, scope replacement, deduplication, and conflict detection.

## Gaps

- Learning is mostly prompt-instructed, not lifecycle-enforced.
- There is no typed distinction between facts, preferences, projects, operating lessons, and
  temporary task notes.
- Memory fragments have timestamps and importance, but no confidence, provenance, validation
  state, or decay policy.
- User corrections and operational failures are not promoted into durable lessons in a systematic
  way.
- Stored memory does not yet strongly affect routing, tool selection, or agent behavior beyond
  prompt injection.
- There is no routine memory audit that tells the user when memory is stale, contradictory, too
  large, or polluted with transient task details.

## Target Model

Memory should become a closed loop:

1. Observe: collect user corrections, repeated preferences, successful workflows, failures, and
   project state changes.
2. Classify: decide whether each signal is a fact, preference, project state, operating lesson,
   or temporary note.
3. Persist: store durable signals with provenance, confidence, and scope.
4. Apply: use memory to change routing, prompts, skill selection, and maintenance behavior.
5. Verify: periodically check whether stored memory is still true and useful.
6. Prune: remove stale, duplicated, contradicted, or low-value fragments.

## Proposed Schema Direction

Future memory records should include:

- `kind`: `fact`, `preference`, `project_state`, `operating_lesson`, `identity`, `constraint`
- `scope`: `agent`, `shared`, `project`, `user`
- `subject`: concise entity or project key
- `claim`: the durable statement
- `evidence`: source message, file, tool result, or operator note
- `confidence`: numeric or enum
- `status`: `active`, `tentative`, `superseded`, `stale`, `rejected`
- `created_at`, `updated_at`, `last_verified_at`
- `expires_at` or `stale_after` where appropriate

The existing `memory_fragments` table can remain the compatibility layer while typed memory is
introduced incrementally.

## Agent Health Integration

The health agent should inspect memory as part of regular self-diagnosis:

- Was `MAINMEMORY.md` updated recently?
- Is `SHAREDMEMORY.md` still short and alert-like?
- Are there contradictory preference fragments?
- Are recent operational failures missing from memory?
- Is memory being used as a task dump instead of durable context?

This diagnostic should produce a user alert only when there is a real action to take. Otherwise it
should return the heartbeat ACK token.

## Near-Term Plan

1. Keep Markdown memory as the human-editable source of truth.
2. Add a memory audit report before changing persistence semantics.
3. Teach the maintenance agent to summarize repeated failures into operating lessons.
4. Add typed memory records only after the audit output is stable.
5. Wire high-confidence operating lessons into routing and health diagnostics.
