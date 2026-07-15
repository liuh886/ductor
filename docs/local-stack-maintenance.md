# Local Stack Maintenance

`codex/local-stack` is the daily runtime. Keep it as a small linear layer over
`upstream/main`; use `codex/upstream-fixes` only for clean upstream PR commits.

Run this flow weekly, before a runtime cutover, and after an upstream release.

## 1. Fetch And Measure

```powershell
git fetch upstream --prune
git fetch origin --prune
git rev-list --left-right --count upstream/main...HEAD
git log --oneline upstream/main..HEAD
git diff --stat upstream/main...HEAD
python scripts/local_stack_audit.py
```

Classify each new local commit as one of:

- upstreamable bug fix
- required local runtime behavior
- temporary experiment to remove

The audit is read-only. Before the first deployment from a new worktree, use
`--skip-runtime`; after deployment, the normal audit must identify the exact
worktree, commit, and PID serving Ductor.

## 2. Align Main

Only align the local and fork `main` after every non-upstream commit has either
been retained in local-stack or intentionally dropped.

```powershell
git switch main
git merge --ff-only upstream/main
git push --force-with-lease origin main
```

The force-with-lease step intentionally replaces fork-only `main` history. Do
not run it while the local-stack commit inventory is incomplete. The audited
14-commit disposition is recorded in `local-stack-origin-main-inventory.md`.

## 3. Rebase The Stack

Use a clean rebase when upstream remains an ancestor and conflicts are small:

```powershell
git switch codex/local-stack
git rebase upstream/main
```

If the old branch contains merges or WIP history, rebuild instead:

1. create a new branch from `upstream/main`
2. apply still-open upstream PR commits
3. apply only required local runtime commits
4. compare behavior and commit count before replacing `codex/local-stack`

Current required local-only areas are MiMo gateway support, explicit read-only
vault retrieval, automatic-memory defaults, runtime identity, and maintenance
guardrails. Antigravity, transport fixes, context recovery, and privacy fixes
should move upstream when their PRs are accepted.

## 4. Verify

Run targeted checks first:

```powershell
pytest tests/cli/test_antigravity_provider.py tests/orchestrator/test_model_selector.py -q
pytest tests/orchestrator/test_flows.py tests/messenger/telegram -q
pytest tests/multiagent tests/test_vault_index_sync.py -q
python -m scripts.vault_retrieval_benchmark --fixture scripts/vault_retrieval_cases.json --index $HOME\.ductor\workspace\memory_system\vault_index.db
python -m ruff check ductor_bot tests scripts
python -m mypy ductor_bot
```

Before replacing the daily runtime, run the complete suite:

```powershell
pytest -q
```

Confirm these high-cost features are disabled unless being tested explicitly:

- `heartbeat.enabled`
- `memory_context.enabled`
- `memory_flush.enabled`
- `memory_reflection.enabled`
- `memory_compaction.enabled`
- `append_system_prompt_files` (must be empty for the minimal stack)

Durable knowledge comes from the zhihaol Markdown vault through explicit
`vault_search.py` calls. `vault_index.db` is a rebuildable read-only projection.
The checked-in retrieval fixture is a local-stack regression gate: keyword,
paraphrase, abstention, source-path, and excerpt checks must all pass. It contains
queries and expected paths only, never vault note content.
The audit also rejects `SHAREDMEMORY.md`, retired `state.db`/memory-tool surfaces, and the obsolete
`state_backend`/`state_db_path` configuration keys in active agent homes. Archived
migration evidence under `~/.ductor/archive/` is intentionally ignored.

## 5. Cut Over And Observe

The PM2 configuration uses `__dirname`, so a newly registered process runs the
checkout that contains `ecosystem.config.js`. PM2 preserves the existing `cwd`
when `startOrReload` reloads an app, however, so a cross-worktree cutover must
delete the old app definition first.

```powershell
pm2 delete ductor
$env:DUCTOR_PYTHON = (Get-Command python).Source
$env:DUCTOR_CODEX_HOME = "$HOME\.ductor\provider_homes\codex"
pm2 start ecosystem.config.js --only ductor --update-env
pm2 status ductor
python scripts/local_stack_audit.py
```

`DUCTOR_CODEX_HOME` is optional. The local runtime uses an isolated Codex home
with shared authentication and disables Codex plugins, reducing fixed input
cost for a trivial deployed turn from about 20.5k to 14.5k tokens in the July 15 probe.
Provision its `auth.json` as a link to the host Codex auth file before the first
isolated start. Do not copy provider session history into the isolated home;
stale Ductor session IDs recover once into a fresh provider session.

Set `skills.sync_enabled=false` and Codex CLI parameters `--disable plugins` in
the main config and every active agent config. A child agent with the default
sync setting can repopulate the isolated home during startup. The local-stack
audit verifies both settings across all registered agent homes.

For a later restart from the same checkout, `pm2 startOrReload` is sufficient.
Never accept a successful PM2 status as proof of a cutover; the audit must pass
both `runtime checkout` and `runtime commit`.

Inspect `~/.ductor/logs/agent.log` after startup. Verify:

- each configured transport reaches polling/connected state
- `/model` lists expected providers and concrete Antigravity models
- one short turn succeeds for the active provider
- one resumed turn succeeds
- no context-limit retry loop, empty final response, or ProcessRegistry error

Do not diagnose a new source tree against an old process. If runtime identity
does not match `HEAD`, fix the PM2 checkout and restart first.
