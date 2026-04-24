# Ductor Architecture Design: Trinity Memory v3.0 (Hermes Aligned)

## 1. Context Engineering Architecture

The memory system follows a 5-layer "Pyramid" model (L0-L5), optimized for Token efficiency and Prefix Caching.

### Memory Tiers
- **L0/L1 (Soul)**: Core persona and static behavioral rules. (Cached Prefix)
- **L3 (Task Context)**: Active epics and tasks extracted from `TASKS.md`. Used as keywords for filtering.
- **L4 (Shared Context)**: Real-time cross-agent alerts and broadcasts via a virtualized kernel.
- **L5 (Memory Fragments)**: Atomic, time-sortable facts with unique IDs.

## 2. Technical Implementation Details

### Atomic Traceability (ULID)
Every memory fragment is assigned a **Time-Sortable ID** generated via millisecond-precision timestamps. This allows the system to prioritize recent facts over older ones during retrieval.
- Format: `[ID: <hex_timestamp>-<random_suffix>]`

### Cognitive Pruning (Task-Aware Retrieval)
Instead of full file injection, the system performs a **Semantic Score Ranking**:
1. Load active keywords from `TASKS.md`.
2. Rank fragments in `state.db` based on keyword frequency.
3. Inject only the Top-10 most relevant fragments (limited to ~1500 tokens).

### Atomic Manipulation Tools
Agents are granted two specialized capabilities:
- `patch_memory_fragment(ulid, body)`: Updates a specific fact atom.
- `delete_memory_fragment(ulid)`: Removes a redundant or incorrect fact.
*Note: Any change in SQLite triggers a **Reverse Sync** to update the corresponding `.md` file, preserving human-readability.*

## 3. Autonomous Cognitive Maintenance
The system implements a **Wake-on-Sense** loop:
- **Trigger**: Every 20 messages in a session.
- **Action**: A background "Memory Synthesis" agent reviews the history and distills raw conversation into durable L5 fragments.

## 4. Multi-Agent Virtual Federation
Sub-agents operate in isolated workspaces but share a **Global Read-Only Database Attachment** to the root `state.db` for `sharedmemory` scope, ensuring zero-latency knowledge propagation without file conflicts.
