# Self-Check Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the source repo self-check flow by fixing the current Python collection failure and the Windows-specific gstack test failures.

**Architecture:** Reintroduce the shared-knowledge compatibility helpers needed by the Python tests without changing the current runtime no-op watcher behavior. For gstack, fix Windows path handling in tests, remove import-time CLI side effects, and make Node subprocess tests path-safe.

**Tech Stack:** Python, pytest, Bun, TypeScript, Bash hook scripts

---

### Task 1: Restore shared knowledge compatibility helpers

**Files:**
- Modify: `ductor_bot/multiagent/shared_knowledge.py`
- Test: `tests/multiagent/test_shared_knowledge.py`

- [ ] **Step 1: Use the existing failing test as the red state**

Run: `python -m pytest tests/multiagent/test_shared_knowledge.py -q`
Expected: FAIL during import because `_END_MARKER` and related helpers are missing.

- [ ] **Step 2: Add the missing compatibility constants and helper functions**

Implement marker constants, `_find_markers()`, and `_sync_agent_io()` in `ductor_bot/multiagent/shared_knowledge.py` while keeping `SharedKnowledgeSync.sync_agent()` as a no-op.

- [ ] **Step 3: Re-run the targeted test**

Run: `python -m pytest tests/multiagent/test_shared_knowledge.py -q`
Expected: PASS

### Task 2: Fix gstack Windows bash path handling and path splitting

**Files:**
- Modify: `ductor_bot/_home_defaults/workspace/skills/gstack/test/diff-scope.test.ts`
- Modify: `ductor_bot/_home_defaults/workspace/skills/gstack/test/hook-scripts.test.ts`

- [ ] **Step 1: Keep the current red state**

Run: `bun test ductor_bot/_home_defaults/workspace/skills/gstack/test/diff-scope.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/test/hook-scripts.test.ts`
Expected: FAIL with `ENOENT` in `diff-scope.test.ts` and `status 127` from bash script launch in `hook-scripts.test.ts`.

- [ ] **Step 2: Patch the tests**

Use `dirname()` instead of slash slicing in `diff-scope.test.ts`, and add a helper that converts Windows paths to bash-compatible POSIX paths before invoking `bash` in both test files.

- [ ] **Step 3: Re-run the targeted test files**

Run: `bun test ductor_bot/_home_defaults/workspace/skills/gstack/test/diff-scope.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/test/hook-scripts.test.ts`
Expected: PASS

### Task 3: Remove browse CLI import-time side effects and fix Node subprocess path escaping

**Files:**
- Modify: `ductor_bot/_home_defaults/workspace/skills/gstack/browse/src/cli.ts`
- Modify: `ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/bun-polyfill.test.ts`
- Modify: `ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/config.test.ts`

- [ ] **Step 1: Keep the current red state**

Run: `bun test ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/bun-polyfill.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/config.test.ts`
Expected: FAIL because the polyfill path is injected unsafely into Node snippets and `cli.ts` throws at import time on Windows when `server-node.mjs` is absent.

- [ ] **Step 2: Patch the implementation and tests**

Make `cli.ts` resolve server script paths lazily instead of at module import time, and pass the polyfill path into child Node code via `JSON.stringify(...)` so Windows backslashes do not corrupt the script.

- [ ] **Step 3: Re-run the targeted browse tests**

Run: `bun test ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/bun-polyfill.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/config.test.ts`
Expected: PASS

### Task 4: Verify integrated fixes

**Files:**
- Verify only

- [ ] **Step 1: Run the combined targeted verification**

Run: `python -m pytest tests/multiagent/test_shared_knowledge.py -q && bun test ductor_bot/_home_defaults/workspace/skills/gstack/test/diff-scope.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/test/hook-scripts.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/bun-polyfill.test.ts ductor_bot/_home_defaults/workspace/skills/gstack/browse/test/config.test.ts`
Expected: all targeted tests pass

- [ ] **Step 2: Spot-check that no unrelated source files were touched**

Run: `git status --short`
Expected: only the planned files appear
