# Hermes Core Alignment Checklist

This checklist narrows "align with Hermes Agent" down to the core capabilities that materially improve Ductor's quality as a local-first multi-agent orchestrator. It explicitly excludes broad cross-platform gateway parity.

## Scope

In scope:
- provider/runtime reliability
- session and task continuity
- sub-agent correctness
- workspace and skill integrity
- high-signal operator diagnostics

Out of scope:
- new transport platforms
- voice/media parity
- website/docs marketing parity
- full terminal UX parity

## Priority Order

### 1. Provider Error Semantics and Recovery
- [ ] Normalize provider failures into a small shared taxonomy across Claude, Codex, and Gemini.
- [ ] Preserve stderr, return code, timeout state, and parsed error type through `CLIResponse -> AgentResponse -> TaskHub`.
- [ ] Ensure logs expose actionable failure causes without leaking secrets.
- [ ] Add regression tests for timeout, empty stdout, invalid JSON, and non-zero exit cases.

Why this matters:
- Hermes is strong at making model/runtime failures legible and recoverable.
- Ductor currently risks turning provider failures into low-signal "CLI failed" symptoms.

Done when:
- every provider wrapper returns a classified error
- task failures surface the real cause
- the relevant provider tests pass

### 2. Sub-Agent Config Sync and Hot Reload Correctness
- [ ] Make `agents.json` the operational source of truth for sub-agent auth/model settings.
- [ ] Mirror effective settings into each sub-agent local `config.json` so restarts and hot reloads read the same values.
- [ ] Ensure rebuild/restart paths do not drift from registry state.
- [ ] Add regression tests for config changes that should trigger sync without manual repair.

Why this matters:
- Hermes treats delegated workers as first-class runtimes, not stale forks.
- Ductor quality drops fast if sub-agents silently run old credentials or model settings.

Done when:
- changing sub-agent settings propagates deterministically
- restart/rebuild uses the merged effective config
- supervisor tests cover sync and restart paths

### 3. Task Runtime Reliability and Error Return Quality
- [ ] Upgrade TaskHub error reporting so failed delegated work returns structured, operator-useful failure detail.
- [ ] Preserve task context during CLI failures instead of collapsing to generic messages.
- [ ] Verify forum/topic-aware routing still works when task execution fails.
- [ ] Add targeted tests for failed task execution and parent-session reporting.

Why this matters:
- Hermes gets leverage from delegation because failures are understandable and resumable.
- Ductor delegation is only valuable if operators can tell whether to retry, fix config, or change prompt.

Done when:
- delegated task failures include classified reason and evidence
- parent session receives useful failure summaries
- task hub tests cover both success and failure paths

### 4. Workspace Seeding and Skill Sync Integrity
- [ ] Make workspace init idempotent across normal and sandbox/docker modes.
- [ ] Tighten skill sync rules for linked vs copied installs, isolated skills, and managed markers.
- [ ] Ensure rule/skill deployment does not clobber user-owned files outside managed zones.
- [ ] Add tests for init, cron task seeding, rule sync, and skill sync edge cases.

Why this matters:
- Hermes's core usability comes from a stable home/workspace contract.
- Ductor's runtime quality depends on seeded rules and synced skills being correct every time.

Done when:
- repeated init/sync runs converge without drift
- docker/sandbox paths behave the same way the tests assert
- workspace tests pass on the touched files

### 5. Session-State Operability
- [ ] Improve session/task logs so operators can distinguish provider failure, config failure, and process failure quickly.
- [ ] Keep active-session behavior, timeout classification, and named-session execution internally consistent.
- [ ] Verify no new changes regress session isolation or background execution semantics.

Why this matters:
- Hermes invests heavily in "what state is my agent in right now?"
- Ductor does not need Hermes's full UI, but it does need comparable operational clarity.

Done when:
- logs and tests make session/task failure mode obvious
- no touched session/task tests are flaky or ambiguous

### 6. Cross-Session Recall and Session Search
- [ ] Define the minimum Ductor equivalent of Hermes session search for operator-facing continuity.
- [ ] Ensure session state can be searched or summarized across prior runs without depending on transport expansion.
- [ ] Add tests around session indexing and lookup boundaries.

Why this matters:
- Hermes has a real advantage in searching and reusing prior conversations.
- Without this, Ductor can be reliable but still weaker as an agent runtime with memory continuity.

Done when:
- Ductor has a clearly defined session recall surface
- cross-session retrieval is test-covered and operationally usable

### 7. Restart and Recovery Continuity
- [ ] Verify in-flight turn recovery, named-session recovery, and task recovery semantics after restart.
- [ ] Tighten recovery logging so operators can tell what was resumed, dropped, or requires manual action.
- [ ] Add regression coverage for restart/recovery planning on the current session/task model.

Why this matters:
- Hermes treats agent continuity as a runtime property, not just a chat log.
- Recovery quality directly affects whether Ductor feels dependable under long-running work.

Done when:
- restart behavior is deterministic and explainable
- recovery tests cover the intended continuity contract

## Immediate Execution Plan

Work the current uncommitted batch in this order:

1. Finish provider error taxonomy propagation and tests.
2. Finish sub-agent config mirroring and supervisor tests.
3. Finish TaskHub failure formatting and task tests.
4. Finish workspace/skill sync edge cases and tests.
5. Run the full touched-test suite, then expand only if failures indicate shared regressions.

## Alignment Assessment

This checklist is enough for the current batch to improve Ductor in the same direction as Hermes on:
- runtime reliability
- sub-agent correctness
- task/session operability
- workspace integrity

It is not, by itself, enough for full core parity with Hermes.

For stronger core alignment after this batch, the next required moves are:
- session search / cross-session recall
- restart/recovery continuity

## Non-Goals for This Batch

- No new transport adapters.
- No gateway feature expansion.
- No broad UX redesign.
- No speculative memory/learning loop work unless required by the touched code.
