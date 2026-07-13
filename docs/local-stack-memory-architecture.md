# Local-Stack Memory Architecture

The local runtime uses three narrow context layers and keeps durable knowledge
outside provider prompts unless retrieval is explicitly needed.

## 1. Provider Sessions

Claude, Codex, Gemini, MiMo, and Antigravity own recent conversation context.
Session buckets remain isolated by transport, chat/topic, and provider. Ductor
does not prepend a persisted summary to every new session by default.

## 2. Operational State

Ductor registries store sessions, tasks, inflight turns, automation state, and
other runtime facts. These stores are operational projections, not durable world
knowledge. They must not enable automatic synthesis or prompt injection.

`SHAREDMEMORY.md` is retained only as a short cross-agent operational note for
ports, environment changes, and active incidents. Agents read it explicitly;
it is not copied into agent-local prompts.

## 3. Vault Knowledge

`D:\Documents\zhihaol` is the durable, human-readable knowledge repository.
Markdown is authoritative. `vault_index.db` is a rebuildable read-only projection.

Agents retrieve explicitly with:

```powershell
python tools/agent_tools/vault_search.py "query"
```

The adapter returns bounded excerpts and source paths. A miss returns no context.
It never falls back to unrelated memory, writes the vault, or calls an LLM.

The index maintenance command is read-only unless `--apply` is supplied:

```powershell
python scripts/vault_index_sync.py --vault D:\Documents\zhihaol
```

With `--apply`, it builds and verifies a temporary SQLite index, backs up the
current index, then replaces it atomically. Vault Markdown is never modified.

## Legacy Compatibility

Upstream `MAINMEMORY.md` support remains available but is opt-in:

- `memory_context.enabled=false`
- `memory_flush.enabled=false`
- `memory_reflection.enabled=false`
- `memory_compaction.enabled=false`

Enabling one control does not silently enable the others. New work should use
provider sessions, operational registries, and explicit source-backed retrieval.

## GBrain Boundary

GBrain is useful as a reference for separating session context, agent memory,
and world knowledge. It remains an optional sidecar candidate rather than a
Ductor dependency. Adoption requires better measured multilingual retrieval than
the built-in adapter, no autonomous writes or enrichment by default, citations,
and the ability to disable it without affecting Ductor startup or transports.
