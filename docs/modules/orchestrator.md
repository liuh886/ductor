# orchestrator/

Central routing layer between Telegram UI and CLI execution.

## Files

- `core.py`: `Orchestrator` lifecycle, routing, observer wiring, shutdown
- `registry.py`: `CommandRegistry`, `OrchestratorResult`
- `commands.py`: command handlers (`/status`, `/model`, `/cron`, `/diagnose`, `/upgrade`, ...)
- `flows.py`: normal flow, streaming flow, heartbeat flow, error/sigkill handling
- `directives.py`: leading `@...` directive parser
- `hooks.py`: hook registry + `MAINMEMORY_REMINDER`
- `model_selector.py`: interactive model/provider switch wizard (`ms:*`)
- `cron_selector.py`: interactive cron toggles (`crn:*`)

## Startup (`Orchestrator.create`)

1. `init_workspace(paths)`
2. set `DUCTOR_HOME`
3. optional Docker setup (`DockerManager.setup`)
4. if Docker active: re-sync skills in copy mode (`docker_active=True`)
5. inject runtime environment notice into workspace rule files
6. construct orchestrator instance
7. detect provider auth (`check_all_auth`) and update available providers
8. start model caches (`_init_model_caches`):
   - `GeminiCacheObserver` (`gemini_models.json`) with refresh callback to `set_gemini_models`
   - `CodexCacheObserver` (`codex_models.json`)
9. construct/start `CronObserver`, `HeartbeatObserver`, `WebhookObserver`, `CleanupObserver`
10. start rule sync watcher (`watch_rule_files`)
11. start skill sync watcher (`watch_skill_sync`)

## Routing entry points

- `handle_message(chat_id, text)`
- `handle_message_streaming(chat_id, text, callbacks...)`

Shared path:

- clear abort flag
- log suspicious input patterns (no hard block here)
- command dispatch first
- fallback to directive + normal/streaming flow
- domain/unexpected exception boundary returns generic error text

## Command registry

Registered commands:

- `/new`
- `/status`
- `/model`
- `/model ` (prefix form)
- `/memory`
- `/cron`
- `/diagnose`
- `/upgrade`

`/stop` is intentionally not registered here; abort is middleware/bot-level behavior.

## Directives

`parse_directives(text, known_models)` parses only leading `@...` tokens.

Known model IDs are refreshed from:

- Claude set (`haiku`, `sonnet`, `opus`)
- Gemini aliases (`auto`, `pro`, `flash`, `flash-lite`)
- discovered Gemini models from runtime cache

Codex IDs are not included in inline directive-known set.

Directive-only model messages return guidance text instead of executing.

## Normal/streaming flow (`flows.py`)

`_prepare_normal()`:

- resolve runtime model/provider target
- resolve or create session with provider-isolated buckets
- new session: append `MAINMEMORY.md` as system appendix
- apply hooks
- build `AgentRequest`

Gemini safeguard:

- if target provider is Gemini,
- and Gemini auth mode is API-key,
- and `gemini_api_key` in config is empty/`"null"`,
- return warning result without spawning CLI.

Error behavior:

- SIGKILL: reset only active provider bucket and retry once
- other errors: kill processes, preserve session, return session-error guidance

Success behavior:

- persist returned session ID
- increment counters/cost/tokens
- optional session-age note every 10 messages after threshold

## Heartbeat flow

`heartbeat_flow` is read-only until non-ACK output:

- skip when no active session or no `session_id`
- skip when provider mismatch or cooldown not reached
- run heartbeat prompt in existing session
- strip ACK token (`HEARTBEAT_OK` by default)
- only non-ACK responses update session and trigger delivery

Observer wiring in `Orchestrator.__init__`:

- busy check callback -> `ProcessRegistry.has_active` (heartbeat skips while chat has active CLI process)
- stale cleanup callback -> `ProcessRegistry.kill_stale(config.cli_timeout * 2)` (run before heartbeat ticks)

## Model selector (`model_selector.py`)

Callback namespace: `ms:`

- provider step: `ms:p:<provider>`
- model step: `ms:m:<model>`
- codex reasoning step: `ms:r:<effort>:<model>`
- back: `ms:b:*`

Behavior:

- provider buttons shown only for authenticated providers
- model list sources:
  - Claude static list
  - Codex cache
  - Gemini discovered models
- switch updates config + CLIService defaults
- provider session buckets are preserved across switches

## Cron selector (`cron_selector.py`)

Callback namespace: `crn:`

- supports paging, refresh, per-job toggle, bulk all-on/all-off
- toggles persist in `CronManager` and call `CronObserver.reschedule_now()`

## Webhook wiring

`Orchestrator` only wires handlers; wake execution remains in bot layer:

- `set_webhook_result_handler`
- `set_webhook_wake_handler`

This keeps wake dispatch behind the same per-chat lock as normal messages.

## Shutdown

`Orchestrator.shutdown()`:

1. cancel rule/skill watcher tasks
2. `cleanup_ductor_links(paths)`
3. stop heartbeat/webhook/cron/cleanup observers
4. stop codex and gemini cache observers
5. teardown Docker container (if managed)
