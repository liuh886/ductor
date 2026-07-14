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

`SHAREDMEMORY.md` is not part of the local runtime. User-owned historical files
must be archived before enabling this stack; the maintenance audit rejects an
active copy so an older runtime cannot silently restore shared-memory behavior.

## 3. Vault Knowledge

`D:\Documents\zhihaol` is the durable, human-readable knowledge repository.
Markdown is authoritative. `vault_index.db` is a rebuildable read-only projection.

Agents retrieve explicitly with:

```powershell
python tools/agent_tools/vault_search.py "query"
```

The adapter returns bounded excerpts and source paths. A miss returns no context.
It never falls back to unrelated memory, writes the vault, or calls an LLM.
The built-in adapter is lexical retrieval, not semantic memory. English uses
FTS5 BM25; Chinese uses overlap-scored 2/3/4-character terms. This keeps the
runtime local and dependency-free while allowing partial Chinese paraphrases.

The index maintenance command is read-only unless `--apply` is supplied:

```powershell
python scripts/vault_index_sync.py --vault D:\Documents\zhihaol
```

The audit compares source checksums as well as paths, so changed Markdown cannot
be reported as current merely because its filename is unchanged. With `--apply`,
it builds and verifies a temporary SQLite index, backs up the current index,
then replaces it atomically. Vault Markdown is never modified.

## Legacy Compatibility

Upstream `MAINMEMORY.md` support remains available but is opt-in:

The same `memory_context.enabled` gate enables one-time cleanup support for old
shared-memory projections. No shared-memory write tool is seeded into new
workspaces; the compatibility module is not part of the active architecture.
- `memory_context.enabled=false`
- `memory_flush.enabled=false`
- `memory_reflection.enabled=false`
- `memory_compaction.enabled=false`

Enabling one control does not silently enable the others. New work should use
provider sessions, operational registries, and explicit source-backed retrieval.

## GBrain Boundary

GBrain is useful as a reference for separating replaceable agent operations from
durable world knowledge, hybrid retrieval, and source-backed answers. Its daemon,
autonomous enrichment cycle, database stack, and large skillpack are deliberately
not copied into Ductor. A future semantic sidecar must be optional, read-only by
default, independently benchmarked, and absent from the startup-critical path.

The current index audit covers 1,511 of 1,511 indexable Markdown documents after
retiring three A-MEM anchor projections. The July 12 private baseline reported
lexical recall@5 of 0.90, semantic-paraphrase recall@5 of 0.30, and abstention
precision of 1.00. That private fixture is diagnostic evidence, not a repository
regression gate. The built-in adapter remains unsuitable for automatic prompt
injection or synthesis; any semantic sidecar must beat a checked-in benchmark,
preserve source citations, and remain independently removable.
