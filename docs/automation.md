# Automation Quickstart

ductor automation systems:

| System | Trigger | Execution Context | Output |
|---|---|---|---|
| Background (`/bg`) | user command | workspace (one-shot) | Telegram notification |
| Cron jobs | schedule | isolated task folder | Telegram result |
| Webhooks | HTTP POST | wake or isolated `cron_task` | Telegram result |
| Heartbeat | interval | active main session | Telegram alert (non-ACK only) |
| Cleanup | daily hour | filesystem maintenance | no Telegram message |

## Background tasks (`/bg`)

`/bg <prompt>` runs a one-shot CLI task in the background. The chat is free immediately; a new Telegram message is sent when the task completes (triggering a push notification).

Key properties:

- uses the current provider/model (same as normal conversation)
- one-shot execution via `build_cmd()` + `execute_one_shot()` (reuses cron execution infrastructure)
- no session persistence -- each `/bg` task is stateless
- result message replies to the original `/bg` message for context
- `/stop` cancels all background tasks for the chat (via asyncio task cancellation)
- max 5 concurrent tasks per chat
- `/status` shows active background tasks

Status values: `success`, `error:timeout`, `error:exit_<code>`, `error:cli_not_found`, `error:internal`, `aborted`.

## Cron jobs

Cron jobs run in `~/.ductor/workspace/cron_tasks/<task_folder>/`.

Each run is a fresh one-shot subprocess in the task folder. It does not reuse the main chat session.

Typical task folder:

```text
~/.ductor/workspace/cron_tasks/weather-report/
  CLAUDE.md
  AGENTS.md
  TASK_DESCRIPTION.md
  weather-report_MEMORY.md
  scripts/
```

Rule files are kept in sync automatically (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) based on newest mtime per directory.

Rule-file sync behavior (all workspace directories, recursive):

- files: `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`
- source of truth per directory: newest file by mtime
- sync runs at init (`sync_rule_files`) and continuously (`watch_rule_files`, every 10s)
- result: edits are propagated to older existing sibling rule files (missing siblings are not auto-created)

Runtime behavior:

1. dependency lock (`dependency_queue`)
2. quiet-hour check (`job.quiet_*` fallback global heartbeat quiet hours)
3. folder check
4. resolve task overrides (`provider/model/reasoning/cli_parameters`)
5. build provider command (`claude`, `codex`, or `gemini`)
6. execute with timeout (`cli_timeout`)
7. parse output and persist status
8. send result callback to Telegram

Per-job override fields in `cron_jobs.json`:

```json
{
  "provider": "gemini",
  "model": "gemini-2.5-pro",
  "reasoning_effort": null,
  "cli_parameters": ["--debug"],
  "quiet_start": 22,
  "quiet_end": 7,
  "dependency": "nightly-reports"
}
```

Notes:

- `reasoning_effort` is only used for Codex models that support it.
- task `cli_parameters` are task-level only (no merge with global provider args).
- cron status includes `error:cli_not_found_<provider>` for missing provider binaries.

## Webhooks

Server route: `POST /hooks/{hook_id}`

Validation order:

1. rate limit
2. content type (`application/json`)
3. JSON object body
4. hook exists
5. hook enabled
6. auth (`bearer` or `hmac`)
7. accept and dispatch asynchronously (`202`)

Modes:

- `wake`: inject rendered prompt into active chat flow
- `cron_task`: run isolated one-shot execution in task folder

Prompt payload is wrapped with safety markers before execution.

`cron_task` mode supports the same override/quiet/dependency fields as cron jobs.

Typical status values:

- `success`
- `error:no_response`
- `error:no_task_folder`
- `error:folder_missing`
- `error:cli_not_found_claude`
- `error:cli_not_found_codex`
- `error:cli_not_found_gemini`
- `error:timeout`
- `error:exit_<code>`
- `skipped:quiet_hours`

## Heartbeat

Heartbeat runs only when `heartbeat.enabled=true`.

Observer behavior:

- interval loop (`interval_minutes`)
- quiet-hour suppression in `user_timezone`
- busy-chat skip via `ProcessRegistry.has_active`
- stale process cleanup hook before each tick

`heartbeat_flow` behavior:

- uses read-only active session lookup,
- skips if no session or no provider-compatible session,
- enforces cooldown via `last_active`,
- sends heartbeat prompt with `resume_session`,
- suppresses pure ACK token responses,
- updates session metrics only for non-ACK alerts.

Default ACK token: `HEARTBEAT_OK`.

Default prompt asks the model to review memory + cron context and either send something useful or respond exactly with `HEARTBEAT_OK`.

## Cleanup

Cleanup runs once per day at `cleanup.check_hour` (in `user_timezone`), checked hourly.

Deletes old files (recursive) from:

- `workspace/telegram_files/`
- `workspace/output_to_user/`
- `workspace/api_files/`

Retention windows:

- `cleanup.telegram_files_days`
- `cleanup.output_to_user_days`
- `cleanup.api_files_days`

Cleanup also prunes empty subdirectories after deletion, so dated upload folders are removed once empty.

## Config blocks

```json
{
  "heartbeat": {
    "enabled": false,
    "interval_minutes": 30,
    "cooldown_minutes": 5,
    "quiet_start": 21,
    "quiet_end": 8,
    "ack_token": "HEARTBEAT_OK"
  },
  "cleanup": {
    "enabled": true,
    "telegram_files_days": 30,
    "output_to_user_days": 30,
    "api_files_days": 30,
    "check_hour": 3
  },
  "webhooks": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 8742,
    "rate_limit_per_minute": 30
  }
}
```

Cron jobs and webhook entries are stored in `cron_jobs.json` / `webhooks.json`.
