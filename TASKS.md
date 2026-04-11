# P0 State Kernel + Context Compression

## Task List Ownership

- [x] Treat `.worktrees/p0-state-kernel/TASKS.md` as the execution checklist for the P0 state-kernel refactor.
- [x] Treat `docs/modules/tasks.md` as module documentation for the delegated task system, not as a second source of task status.
- [x] Keep Hermes-alignment execution status in this file and the dedicated Hermes checklist, not in `docs/modules/tasks.md`.

## Objective

- [ ] Rebuild Ductor's runtime state layer around SQLite/WAL/FTS while preserving current external behavior.
- [ ] Move session, named-session, task, inflight, and message state off fragmented JSON-only persistence.
- [ ] Add a first-class context compression pipeline for normal chat, named sessions, task resume, and inter-agent resume.
- [ ] Add Phase 0.6: Autonomous Skill Evolution and SOUL alignment to reach Hermes-Agent capability baseline.

## Completion Criteria

- [x] `ductor_bot` can run with `state_backend=dual` and mirror runtime state into SQLite without regressions.
- [x] Session, named session, and task reads can switch to SQLite via feature flag while keeping existing user-facing commands working.
- [x] Message-level persistence, session lineage, and reusable summaries exist in the runtime state layer.
- [x] Compression hooks run before prompt construction on the main high-leverage execution paths.
- [x] Tests cover normal chat, named session follow-up, task create/resume, and inter-agent session recovery on the new state layer.

## Evidence Baseline

- [x] `ductor_bot/session/manager.py` is still JSON-based persistence.
- [x] `ductor_bot/session/named.py` persists named sessions to JSON.
- [x] `ductor_bot/tasks/registry.py` persists tasks to `tasks.json`.
- [x] `ductor_bot/tasks/hub.py` uses in-memory `_in_flight` plus registry persistence.
- [x] `ductor_bot/orchestrator/flows.py` still appends `MAINMEMORY.md` directly on new sessions.

## Execution Rules

- [x] Keep all implementation work on branch `feat/p0-state-kernel` inside `.worktrees/p0-state-kernel`.
- [x] Preserve current external interfaces until a phase explicitly switches a read path.
- [x] Prefer additive changes and feature flags over destructive rewrites.
- [x] Run tests before claiming a phase is complete.

## Phase Dependency Map

- [x] Phase 0.1 must land before any read-path migration.
- [x] Phase 0.2 depends on 0.1.
- [x] Phase 0.3 depends on 0.2 for repository-backed state, but message-table scaffolding may start earlier behind unused code paths.
- [x] Phase 0.4 depends on 0.3 for message and summary state.
- [x] Phase 0.5 depends on 0.4 for fragment-backed prompt construction.
- [ ] Phase 0.6 depends on 0.5 for automated skill extraction and MCP support.

## Phase 0.1 - SQLite Kernel Scaffolding + Dual-Write

### Goal

- [ ] Introduce a unified runtime database and repository layer without changing current read behavior.
- [ ] Mirror session, named-session, and task state into SQLite in parallel with existing JSON persistence.

### Key Files

- [x] Create `ductor_bot/runtime/state/db.py`
- [x] Create `ductor_bot/runtime/state/schema.py`
- [x] Create `ductor_bot/runtime/state/migrations/`
- [x] Create `ductor_bot/runtime/state/repositories/session_repo.py`
- [x] Create `ductor_bot/runtime/state/repositories/named_session_repo.py`
- [x] Create `ductor_bot/runtime/state/repositories/task_repo.py`
- [x] Modify `ductor_bot/config.py` to add `state_backend` and DB path config
- [x] Modify `ductor_bot/session/manager.py` to dual-write session updates
- [x] Modify `ductor_bot/session/named.py` to dual-write named-session updates
- [x] Modify `ductor_bot/tasks/registry.py` to dual-write task metadata
- [x] Add tests under `tests/runtime/state/`

### Parallelization

- [ ] Worker A can build DB core, migrations, and repositories first.
- [ ] Worker B can prepare config flag plumbing and repository integration points after repository interfaces are stable.
- [ ] Worker C can draft unit tests for repository CRUD in parallel with Worker A.

### Verification

- [x] Run targeted tests for new repositories.
- [x] Run regression tests for `session`, `tasks`, and `named sessions`.
- [x] Verify `state_backend=json` keeps legacy behavior unchanged.
- [x] Verify `state_backend=dual` creates and updates SQLite rows alongside JSON files.

### Risk Gates

- [ ] No change to current read path yet.
- [ ] No user-facing command output changes.
- [ ] SQLite must use WAL and bounded transaction scopes.
- [ ] If dual-write diverges, log and fail safely instead of silently drifting.

## Phase 0.2 - SQLite Read Paths for Session / Named Session / Task State

### Goal

- [ ] Switch primary reads for session, named session, and task state to repositories backed by SQLite.
- [ ] Keep JSON as compatibility export/materialization only.

### Key Files

- [x] Modify `ductor_bot/session/manager.py` to read from `SessionRepository`
- [x] Modify `ductor_bot/session/named.py` to read from `NamedSessionRepository`
- [x] Modify `ductor_bot/tasks/registry.py` to read from `TaskRepository`
- [x] Modify `ductor_bot/tasks/hub.py` to rely on DB-backed task metadata while keeping in-memory runtime acceleration
- [x] Add compatibility helpers if JSON export remains needed
- [x] Extend tests in `tests/session/`, `tests/tasks/`, `tests/orchestrator/`

### Parallelization

- [ ] Session and named-session migration can proceed in parallel after repository contracts are fixed.
- [ ] Task registry migration should be coordinated with `TaskHub` behavior tests.

### Verification

- [x] `/sessions`-related logic still works through the new read path.
- [x] `/tasks`-related logic still works through the new read path.
- [x] Named session recovery behavior remains compatible across restarts.
- [x] Task restart downgrade semantics remain correct.

### Risk Gates

- [x] Preserve storage-key semantics from `ductor_bot/session/key.py`.
- [x] Preserve provider-bucket semantics from `SessionData.provider_sessions`.
- [x] Preserve current downgrade behavior for stale `running` sessions/tasks.

## Phase 0.3 - Message Persistence + Lineage + Process Facts

### Goal

- [x] Persist message-level runtime facts for normal chat, named sessions, tasks, and inter-agent sessions.
- [x] Introduce session lineage so compression and recovery become non-destructive.

### Key Files

- [x] Create `ductor_bot/runtime/state/repositories/message_repo.py`
- [x] Create `ductor_bot/runtime/state/repositories/process_repo.py`
- [x] Create `ductor_bot/runtime/state/repositories/tool_call_repo.py`
- [x] Create `ductor_bot/runtime/state/repositories/message_repo.py`
- [x] Modify `ductor_bot/orchestrator/flows.py` to record runtime message/process facts for normal, named-session, and heartbeat paths
- [x] Modify `ductor_bot/tasks/hub.py` to persist process/task execution facts
- [x] Modify `ductor_bot/orchestrator/injection.py` as the inter-agent integration layer to persist inter-agent session/process facts
- [x] Modify inflight/recovery plumbing under `ductor_bot/infra/` if needed
- [x] Add tests under `tests/orchestrator/`, `tests/tasks/`, `tests/multiagent/`

### Parallelization

- [ ] Worker A can implement message schema and repository.
- [ ] Worker B can wire normal/named-session orchestration paths.
- [ ] Worker C can wire task/process persistence after Worker A lands process repository interfaces.

### Verification

- [x] Normal chat creates message rows with correct role ordering.
- [x] Named-session follow-up records a distinct source kind.
- [x] Task create/resume writes process and task-turn facts.
- [x] Inter-agent `ia-*` work has persistent runtime message/process metadata.
- [x] Inflight/recovery state can persist through the runtime SQLite layer.

### Risk Gates

- [ ] Do not break provider resume semantics while adding lineage.
- [ ] Avoid double-counting tokens/cost between `messages` and provider session aggregates.
- [ ] Preserve current timeout/abort behavior from `ductor_bot/orchestrator/flows.py`.

### Remaining Slice

- [x] Add session lineage metadata to runtime sessions so compression and recovery can branch non-destructively.
- [x] Record lineage root/parent/depth/reason without changing current storage-key semantics.
- [x] Add targeted tests for lineage creation on session reset/renewal paths.

## Phase 0.4 - Context Compression Kernel

### Goal

- [x] Add reusable compression and prompt-hygiene primitives above the new state layer.
- [x] Replace raw long-history prompt stuffing with summary + protected tail + selective memory retrieval.

### Key Files

- [x] Create `ductor_bot/runtime/compression/context_compressor.py`
- [x] Create `ductor_bot/runtime/compression/summary_selector.py`
- [x] Create `ductor_bot/runtime/compression/tool_output_pruner.py`
- [x] Modify `ductor_bot/orchestrator/flows.py` to invoke compression before prompt build
- [x] Modify `ductor_bot/tasks/hub.py` to summarize before task resume
- [x] Modify inter-agent follow-up path to summarize `ia-*` sessions before resume
- [x] Add tests under `tests/runtime/compression/` and update orchestrator/task tests

### Parallelization

- [ ] Worker A can implement summary and pruning primitives.
- [ ] Worker B can integrate normal-flow prompt construction.
- [ ] Worker C can integrate task/inter-agent resume hygiene once summary APIs are stable.

### Step Breakdown

- [x] Phase 0.4a: add `session_summaries` repository and deterministic summary generation over persisted `messages`.
- [x] Phase 0.4a: add `tool_output_pruner` and protected-tail selection helpers.
- [x] Phase 0.4a: integrate compression context into normal-session, named-session, and inter-agent follow-up prompt construction.
- [x] Phase 0.4a: add focused tests for summary generation, pruned tail preservation, and prompt augmentation.
- [x] Phase 0.4b: integrate compression context into task resume without changing task external UX.
- [x] Phase 0.4b: extend tests for task resume summary injection and regression coverage.

### Verification

- [x] Protected tail is preserved on long sessions.
- [x] Large tool outputs are pruned in prompt context but remain stored in DB.
- [x] Task resume uses structured summary rather than dumping `TASKMEMORY.md`.
- [x] Inter-agent follow-up resumes from compacted state without losing current question chain.

### Risk Gates

- [ ] No compression should destroy the original message record.
- [ ] Compression must be reversible at the state layer via lineage and coverage metadata.
- [ ] Prompt quality regressions must be caught with path-specific tests.

## Phase 0.5 - Markdown Memory Repositioning + Fragment Retrieval

### Goal

- [x] Keep Markdown memory files as human-editable interfaces while moving runtime retrieval to structured fragments.
- [x] Stop treating `MAINMEMORY.md` and `TASKMEMORY.md` as the only runtime truth.

### Key Files

- [x] Create `ductor_bot/runtime/state/repositories/memory_fragment_repo.py`
- [x] Create `ductor_bot/runtime/memory/extractor.py`
- [x] Modify `ductor_bot/workspace/loader.py` and/or memory loading path to support fragment retrieval
- [x] Modify `ductor_bot/orchestrator/flows.py` prompt assembly to prefer fragments + summaries
- [x] Modify `ductor_bot/tasks/hub.py` result packaging to use structured extraction instead of full `TASKMEMORY.md` dumps
- [x] Add tests around memory extraction and prompt selection

### Parallelization

- [ ] Worker A can build fragment extraction from `MAINMEMORY.md` and `SHAREDMEMORY.md`.
- [ ] Worker B can update prompt construction after fragment APIs exist.
- [ ] Worker C can update task result packaging and task memory handling in parallel.

### Step Breakdown

- [x] Phase 0.5a: add `memory_fragments` repository and deterministic Markdown fragment extraction for `MAINMEMORY.md` and `SHAREDMEMORY.md`.
- [x] Phase 0.5a: add loader support for fragment-backed memory reads with fallback to raw Markdown.
- [x] Phase 0.5a: update new-session prompt assembly to prefer fragment-backed memory context over whole-file injection.
- [x] Phase 0.5a: add focused tests for fragment extraction, persistence, and new-session prompt assembly.
- [x] Phase 0.5b: add task-memory extraction and concise result packaging instead of full `TASKMEMORY.md` dumps.
- [x] Phase 0.5b: extend tests for task result packaging and fragment-backed task memory behavior.

### Verification

- [x] New sessions load relevant fragments instead of whole-file memory where appropriate.
- [x] Shared memory remains editable through existing files.
- [x] Task completions return concise structured results plus artifact paths, not full memory dumps.

### Risk Gates

- [ ] Human editability of Markdown memory must be preserved.
- [ ] Fragment extraction must tolerate stale or partially structured Markdown.
- [ ] Existing shared-knowledge sync behavior must remain intact until explicitly migrated.

## Cross-Phase Verification Matrix

- [x] Unit tests for repository CRUD and migrations
- [x] Regression tests for session lifecycle
- [x] Regression tests for named-session lifecycle
- [x] Regression tests for task create / cancel / resume / question forwarding
- [x] Regression tests for inter-agent sync and async flows
- [x] Restart/recovery tests for stale running task/session downgrade
- [x] Compression tests for protected tail, summary coverage, and tool pruning

## Ralph Loop Control

- [x] Freeze prompt: "Implement P0 unified state kernel + context compression without breaking current Ductor behavior."
- [x] Iterate phase by phase: inspect -> implement -> verify -> fix -> re-verify.
- [x] Do not mark a phase complete until its verification bullets are green.
- [x] If a phase blocks, record the blocker and continue with independent preparatory work only.

## Current Execution Queue

- [x] Get architecture checklist finalized into this file.
- [x] Implement Phase 0.1 database scaffolding and dual-write.
- [x] Verify Phase 0.1 with targeted tests before Phase 0.2 starts.
- [x] Start Phase 0.2 read-path migration for session / named session / task state.
- [x] Verify Phase 0.2 session/named-session/task SQLite read paths with targeted regression tests.
- [x] Start Phase 0.3 runtime message/process integration in orchestrator, task hub, and inter-agent paths.
- [x] Verify Phase 0.3 runtime message/process persistence across task, normal flow, heartbeat, and inter-agent paths.
- [x] Finish the remaining Phase 0.3 lineage metadata slice.
- [x] Start Phase 0.4a context compression primitives and orchestrator prompt integration.
- [x] Follow with Phase 0.4b task-resume compression once 0.4a is green.
- [x] Start Phase 0.5a fragment-backed memory retrieval and markdown-memory repositioning.
- [x] Follow with Phase 0.5b task-memory extraction and concise result packaging.

## Latest Verification

- [x] `pytest -q tests/runtime/state/test_db.py tests/runtime/state/test_repositories.py tests/session/test_manager.py`
- [x] `ruff check ductor_bot/session/manager.py ductor_bot/runtime/state/db.py ductor_bot/runtime/state/repositories/session_repo.py tests/runtime/state/test_db.py tests/runtime/state/test_repositories.py tests/session/test_manager.py --ignore C901,PLR0911,PLR0915`
- [x] `pytest -q tests/runtime/skills tests/runtime/memory tests/runtime/state tests/tasks/test_hub.py tests/tasks/test_models.py tests/tasks/test_registry.py tests/orchestrator/test_flows.py tests/orchestrator/test_interagent.py tests/session/test_manager.py tests/session/test_named_recovery.py tests/workspace/test_loader.py tests/workspace/test_skill_sync.py tests/infra/test_inflight.py tests/infra/test_recovery.py` -> `326 passed`

## Repair Checklist (Post-Review)

- [x] Fix SQLite task persistence round-trip so `TaskEntry.original_prompt` and other persisted fields survive `TaskRepository.replace_all()` -> `list_all()`.
- [x] Make successful task skill extraction non-blocking for parent result delivery; extraction must not hijack task completion or resume-path assertions.
- [x] Change extracted skill output to the actual Ductor skill format: `workspace/skills/<slug>/SKILL.md`, not loose `*.md` files.
- [x] Make extracted skill generation deterministic against the completed task context by pinning provider/model behavior explicitly.
- [x] Resolve pytest collection collision between `tests/runtime/memory/test_extractor.py` and `tests/runtime/skills/test_extractor.py`.
- [x] Re-run the runtime/state + task/orchestrator verification suite and keep this file's status aligned with actual green results.

## Phase 0.6 - Autonomous Skill Evolution & Soul Alignment (Hermes-Agent Parity)

### Current Status

- [~] Phase 0.6 has a verified first slice complete: skill extraction now writes discoverable `skills/<slug>/SKILL.md`, runs after parent delivery, and passes the current verification suite.
- [x] Autonomous skill extraction is compatible with the live skill discovery contract and no longer blocks task-completion delivery.
- [~] `SOUL.md` is loaded into prompt assembly, but there is not yet a stronger parsing/behavior contract proving meaningful alignment control.
- [ ] Native MCP runtime bridging is not implemented in this branch.

### Goal

- [ ] Implement an automated `Skill Extractor` that converts successfully executed multi-turn tasks into reusable `.md` workflows in `skills/`.
- [ ] Introduce a `SOUL.md` or equivalent core identity configuration to align agent behavior beyond standard system prompts.
- [ ] Add native Model Context Protocol (MCP) support to the `ductor_bot` ecosystem to dynamically extend capabilities.

### Key Files

- [x] Create `ductor_bot/runtime/skills/extractor.py`
- [x] Modify `ductor_bot/tasks/hub.py` to trigger the `extractor.py` on task completion.
- [~] Create `workspace/SOUL.md` parsing logic in `ductor_bot/workspace/loader.py`
- [ ] Create `ductor_bot/mcp/client.py` for MCP bridging.

### Verification

- [x] Completing a complex task autonomously generates a new discovered skill under `skills/<name>/SKILL.md` reflecting the process.
- [x] Task completion still returns promptly to the parent even when skill extraction runs.
- [ ] The agent's conversational style and long-term goal adherence visibly change when `SOUL.md` is modified.
- [ ] The bot can discover and use a test MCP tool without manual python script creation.
