# Role And Prompt Contract Design

## Goal

Make `ductor` agents feel more professional by separating role identity, routing policy, operational policy, memory context, and situation context.

## Decisions

### 1. Prompt Contract

Runtime prompt assembly is organized into these layers:

- `Role Identity`
- `Routing Policy`
- `Operational Policy`
- `Memory Context`
- `Situation Context`

Each layer has one job and should not absorb adjacent responsibilities.

### 2. Orchestrator-First Policy

Every agent acts as a specialist with orchestration judgment:

- answer directly when one high-quality response is enough
- prefer `capability-router` and delegated workers when work is multi-step, cross-functional, long-running, or deliverable-oriented
- keep user-facing orchestration only moderately visible

### 3. Config Contract

Agent role behavior now has explicit config fields:

- `role`
- `role_description`
- `style_policy`
- `direct_answer_policy`
- `routing_policy`
- `forbidden_modes`

### 4. Memory Contract

`MAINMEMORY.md` stores durable agent-specific context.

`SHAREDMEMORY.md` stores short cross-agent alerts only.

`memory_fragments` remains the runtime authority; Markdown remains projection and editing surface.

### 5. Seismic Role Revision

`seismic-bot` is no longer framed as a signal-monitoring specialist.

It is positioned as a geophysics company CEO / business lead focused on:

- business judgment
- commercial opportunities
- delivery capability
- capability building
- routing deep technical work to specialists

## Implementation Scope

- update prompt assembly in `orchestrator/flows.py`
- update context headings in `cli/context_builder.py`
- soften reminder hooks in `orchestrator/hooks.py`
- extend config and sub-agent config with explicit role-contract fields
- revise default role bootstrap content for `seismic-bot`

## Acceptance

- role, routing, and operational policy appear as separate prompt sections
- hooks bias behavior without overpowering role identity
- new config fields round-trip through config and sub-agent merges
- seismic bootstrap content reflects business leadership, not anomaly monitoring
