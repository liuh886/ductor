# cron/

In-process cron scheduling with JSON persistence and one-shot CLI execution.

## Files

- `manager.py`: `CronJob`, `CronManager` CRUD/persistence
- `observer.py`: `CronObserver` scheduling, watcher, execution pipeline
- `execution.py`: provider command builders, result parsing, one-shot subprocess helper
- `dependency_queue.py`: shared dependency locks (cron + webhook cron_task)

## Cron job model

Core fields:

- `id`, `title`, `description`, `schedule`
- `task_folder`, `agent_instruction`, `enabled`
- `timezone` (optional per-job IANA override)
- `created_at`, `last_run_at`, `last_run_status`

Execution overrides:

- `provider`
- `model`
- `reasoning_effort`
- `cli_parameters`

Scheduling guards:

- `quiet_start`, `quiet_end`
- `dependency`

## Persistence

File: `~/.ductor/cron_jobs.json`

- format: `{ "jobs": [...] }`
- atomic writes via temp+replace

## Observer lifecycle

`start()`:

1. schedule enabled jobs
2. start mtime watcher loop

Watcher:

- polls file mtime every 5s
- on change: reload + full reschedule

`reschedule_now()` is used by interactive cron toggles and updates mtime baseline first to avoid watcher race.

## Execution path

When a job fires:

1. acquire dependency lock when configured
2. quiet-hour gate (`job quiet_*` fallback global heartbeat quiet hours)
3. resolve/validate task folder (`workspace/cron_tasks/<task_folder>`)
4. resolve `TaskExecutionConfig` via `resolve_cli_config(...)`
5. enrich prompt with `<task_folder>_MEMORY.md` instructions
6. build provider command (`build_cmd`)
7. execute one-shot subprocess with timeout
8. parse provider output
9. update run status
10. invoke optional result callback
11. schedule next occurrence

## Command builders (`execution.py`)

Supported providers:

- Claude
- Codex
- Gemini

Examples:

- Claude: `claude -p --output-format json ... --no-session-persistence -- <prompt>`
- Codex: `codex exec --json ... -- <prompt>`
- Gemini: `gemini --output-format json --include-directories . ... -- <prompt>`

`bypassPermissions` behavior:

- Codex: `--dangerously-bypass-approvals-and-sandbox`
- Gemini: `--approval-mode yolo`

## Status values

Typical values:

- `success`
- `error:folder_missing`
- `error:cli_not_found_claude`
- `error:cli_not_found_codex`
- `error:cli_not_found_gemini`
- `error:timeout`
- `error:exit_<code>`

Quiet-hour skip currently does not write `last_run_status`.

## Timezone resolution

Per-job scheduling resolution:

1. `CronJob.timezone`
2. global `user_timezone`
3. host timezone
4. UTC fallback

Cron expressions are evaluated in resolved local wall-clock time.

## Dependency queue

Shared queue key behavior:

- same dependency key -> FIFO serialization
- different/no key -> parallel execution
- shared with webhook `cron_task` runs

## Telegram interaction

`/cron` uses interactive selector (`crn:*` callbacks):

- paging
- refresh
- per-job enable/disable
- bulk all-on/all-off
