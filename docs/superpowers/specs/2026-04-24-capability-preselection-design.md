# Capability Preselection Design

## Goal

Introduce a provider-agnostic capability preselection layer between the orchestrator and provider adapters so `ductor` can reduce unnecessary context exposure before each model invocation.

The immediate driver is Gemini latency. Gemini CLI performs global skill discovery and injects all visible skill metadata plus directory context into the prompt. `ductor` currently amplifies this by syncing many workspace skills into Gemini-visible locations and always passing `--include-directories .`.

The design should let Gemini behave closer to Codex:

- discover a broad capability catalog outside the active turn
- select a small subset of relevant capabilities for the current turn
- expose only the selected capabilities and directory context to the provider runtime

This must be implemented as a shared orchestration layer, not as Gemini-only branching in the provider.

## Non-Goals

- Replacing Gemini CLI internals
- Rewriting the existing skill system format
- Turning `capability-router` into a mandatory state machine for every request
- Requiring `DESIGN.md`, `TASKS.md`, or `EVALUATE.md` for normal chat turns

## Problem Statement

Current behavior has three coarse-grained exposure points:

1. `workspace.skill_sync.sync_skills()` syncs workspace skills into provider-visible global skill directories, including `~/.gemini/skills`.
2. Gemini execution always includes `--include-directories .`, causing directory structure injection even for plain chat.
3. Provider execution has no preselection step, so the provider sees whatever the runtime makes globally visible.

This creates a bad fit for Gemini CLI because Gemini itself:

- globally discovers visible skills
- appends all discovered skill metadata to the prompt
- appends environment and directory structure context when enabled

The result is high token overhead and high wall-clock latency even for trivial requests.

## Design Summary

Add a new orchestration layer: `CapabilityPreselector`.

Per turn, it will:

1. inspect the request, session state, transport context, and workspace state
2. optionally consult `capability-router` for phase and recommended skills
3. resolve a minimal execution profile
4. pass that execution profile to the chosen provider adapter

The provider adapter will no longer infer broad capability visibility on its own. It will consume the preselected execution profile and materialize the smallest provider-specific runtime needed for that turn.

## Architecture

### New Layer

Add a new module family under `ductor_bot/orchestrator/`:

- `capabilities/models.py`
- `capabilities/preselector.py`
- `capabilities/router_adapter.py`
- `capabilities/skill_selector.py`

This layer sits on the call path after high-level orchestration chooses a provider and before provider command construction begins.

### Responsibilities

`CapabilityPreselector` owns:

- phase inference inputs
- capability selection
- directory-context decisions
- runtime profile selection
- provider-facing execution contract

Provider adapters own:

- translating the execution contract into CLI flags, env vars, temp directories, mounts, and files
- provider-specific isolation behavior
- provider-specific fallback behavior when a requested capability cannot be materialized

`capability-router` owns:

- phase recommendation
- recommended role lens
- recommended skill shortlist
- optional state-file participation hints

`capability-router` does not own:

- CLI argument generation
- Docker mount generation
- Gemini runtime directory construction
- direct filesystem mutation outside its own state-file protocol

## Execution Contract

Add a provider-agnostic result object returned by preselection.

Suggested shape:

```python
@dataclass(slots=True)
class CapabilityExecutionPlan:
    provider: str
    phase: str | None
    recommended_role: str | None
    selected_skills: tuple[SelectedSkill, ...]
    include_directories: bool
    directory_scope: tuple[str, ...]
    memory_mode: str
    runtime_profile: str
    needs_workspace_write: bool
    state_files: tuple[str, ...]
    rationale: tuple[str, ...]
```

`SelectedSkill` should carry enough information for provider materialization:

```python
@dataclass(slots=True)
class SelectedSkill:
    name: str
    source_path: str
    activation_kind: str
```

## Selection Pipeline

### Step 1: Gather Inputs

Inputs include:

- current user message
- normalized provider choice
- current session mode
- active transport
- current workspace root
- lightweight task context already available in `ductor`
- optional `TASKS.md` keywords from existing loader utilities

### Step 2: Phase Recommendation

If the turn appears multi-step, stateful, or file-oriented, call `capability-router` through a thin adapter and request:

- `phase`
- `recommended_role`
- `recommended_skills`
- whether state files are relevant

For short conversational turns, skip router invocation and use direct lightweight heuristics.

### Step 3: Skill Selection

Merge:

- router recommendations
- explicit user mentions
- local heuristics based on the request

Then enforce a hard cap. Initial cap:

- default `0-3` selected skills
- may be `0` for plain chat

Selection rules:

- prefer exact user-named skills
- prefer phase-aligned skills
- avoid generic wrappers unless they materially narrow execution
- avoid loading skills solely because they exist globally

### Step 4: Directory Context Decision

Default:

- `include_directories = False`

Enable only when the turn clearly requires local files, repo inspection, or filesystem actions.

When enabled, `directory_scope` should be minimal:

- repo root only by default
- narrower scoped paths when available

### Step 5: Runtime Profile

Profiles should be small enums at first:

- `chat_light`
- `workspace_read`
- `workspace_write`
- `stateful_design`
- `evaluation`

Providers may interpret them differently, but the preselector remains provider-agnostic.

## Gemini Adaptation

Gemini is the first provider that will materially benefit from this layer.

### Runtime Isolation

Gemini execution should use an isolated `GEMINI_CLI_HOME` owned by `ductor`, not the user-global home directory.

This isolated runtime should contain only:

- required auth/config files
- selected per-turn skills when any are chosen
- optional provider-local settings needed for auth mode

It should not expose the user-global `~/.gemini/skills` set by default.

### Skill Materialization

For a given turn:

- create or reuse a temp runtime directory for the effective `CapabilityExecutionPlan`
- materialize only `selected_skills` into `<isolated_home>/.gemini/skills`
- use links on host where safe, copies in Docker mode where required

This preserves Gemini's native skill activation model while shrinking the set of visible skills.

### Directory Context

Gemini should stop always sending `--include-directories .`.

Instead:

- omit it for `chat_light`
- include it only for profiles requiring workspace context
- later enhancement: support narrower provider-specific scope if Gemini CLI exposes a smaller include surface

### Skill Sync Changes

`sync_skills()` should stop treating Gemini like Codex/Claude for default global sync.

New rule:

- Codex and Claude keep current sync behavior
- Gemini no longer receives the full synchronized skill corpus by default
- Gemini runtime skills are materialized per turn from `CapabilityExecutionPlan`

## Codex and Claude Behavior

This layer should not regress Codex or Claude.

Initial behavior:

- they may continue using existing global skill discovery
- providers may ignore parts of the execution plan not yet needed

The architecture should still let them opt into narrower exposure later, using the same contract.

## Capability-Router Role

`capability-router` should be an advisory subsystem, not a mandatory wrapper.

Recommended usage:

- use it when the turn is stateful, multi-step, or likely to benefit from phase reasoning
- skip it for trivial turns where direct heuristics are enough

Router outputs that matter here:

- `phase`
- `recommended_role`
- `recommended_skills`
- `file_update_plan`
- `needs_user_confirmation`

For this feature, only the first three are on the hot path. State-file maintenance remains optional and should not block ordinary provider calls.

## Integration Points

Primary integration path:

1. orchestrator resolves provider
2. orchestrator invokes `CapabilityPreselector`
3. provider receives `CapabilityExecutionPlan`
4. provider builds provider-specific runtime from that plan

Expected touch points:

- orchestrator provider invocation path
- Gemini provider command/env construction
- workspace skill sync policy
- optional cron Gemini one-shot path if it should share the same policy

## Failure Handling

If preselection fails:

- log the failure
- fall back to current provider behavior for that turn
- mark the turn as degraded in debug logs

If router invocation fails:

- continue with heuristic-only selection

If isolated Gemini runtime cannot be materialized:

- fall back to current Gemini execution path
- emit a warning once per process lifecycle or once per configured interval

## Rollout Plan

### Phase 1

- introduce `CapabilityExecutionPlan`
- add `CapabilityPreselector` with heuristic-only selection
- make Gemini honor `include_directories`
- add isolated Gemini runtime support

### Phase 2

- stop default Gemini global skill sync
- add per-turn Gemini skill materialization
- add tests for selected-skill visibility and no-skill chat turns

### Phase 3

- integrate `capability-router` as an optional recommender
- add phase-aware profile selection
- add observability for selected profile and selected skill count

## Testing

Add tests covering:

- plain chat turn yields zero selected skills and no directory context
- repo inspection turn enables directory context
- explicit skill mention selects the named skill
- Gemini isolated runtime contains only selected skills
- Gemini fallback path still works when isolated runtime setup fails
- router failure degrades to heuristic selection

## Risks

### Over-selection

If heuristics are too broad, the new layer becomes another coarse global exposure path.

Mitigation:

- hard cap selected skills
- log rationale
- add tests for common trivial prompts

### Under-selection

If heuristics are too narrow, providers may lose capabilities unexpectedly.

Mitigation:

- safe fallback to existing behavior
- initial rollout behind config flag if needed

### Hidden Coupling

If provider adapters silently reinterpret the plan, the abstraction becomes misleading.

Mitigation:

- keep the contract explicit
- log effective provider materialization in debug mode

## Open Decisions

1. Whether Phase 1 should ship behind a config flag
2. Whether cron Gemini jobs should immediately adopt the same runtime isolation path
3. Whether `state_files` should be fully populated in Phase 1 or left mostly advisory

## Recommendation

Proceed with the shared `CapabilityPreselector` architecture.

It is the smallest design that:

- fixes the Gemini-specific context bloat in a principled way
- avoids provider-specific orchestration logic
- gives `capability-router` a useful advisory role without over-centralizing execution
- preserves a path for Codex and Claude to adopt the same narrowing model later
