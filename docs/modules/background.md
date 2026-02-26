# background/

On-demand background task execution for `/bg` (fire-and-notify workflow).

## Files

- `background/observer.py`: `BackgroundObserver` task lifecycle, execution, cancel/shutdown, result callback
- `background/models.py`: `BackgroundTask`, `BackgroundResult` dataclasses

## Purpose

Runs one-shot CLI tasks without blocking the chat flow:

- user sends `/bg <prompt>`
- task executes asynchronously in workspace context
- bot sends a new completion/failure/cancel message when done

This path is stateless (no main-session persistence).

## Execution model

`BackgroundObserver.submit(...)`:

1. enforces per-chat concurrency cap (`MAX_TASKS_PER_CHAT = 5`)
2. creates `BackgroundTask` metadata (`task_id`, chat/thread IDs, provider/model, prompt preview source)
3. starts `asyncio.create_task(self._run(...))`
4. auto-removes finished tasks from in-memory registry

`_run(...)` delegates execution to `infra/task_runner.run_oneshot_task(...)`:

- command is built from resolved provider/model config
- task runs in `paths.workspace`
- timeout uses `config.cli_timeout`
- status/result normalized into `BackgroundResult`

## Status mapping

Delivered `BackgroundResult.status` values include:

- success path: `success`
- execution failures: `error:timeout`, `error:exit_<code>`, `error:cli_not_found`, `error:internal`
- user abort path: `aborted`

Note:

- internal one-shot execution may emit provider-specific `error:cli_not_found_<provider>`
- `BackgroundObserver` normalizes missing-binary outcomes to `error:cli_not_found`

## Wiring

Orchestrator integration (`orchestrator/core.py`):

- created in `Orchestrator.create(...)`
- submission API: `submit_background_task(...)`
- listing API: `active_background_tasks(...)`
- shared abort: `abort(chat_id)` cancels both CLI subprocesses and active background tasks
- shutdown: `_stop_observers()` calls `BackgroundObserver.shutdown()`

Bot integration (`bot/app.py`):

- `/bg` handler submits task and confirms start to user
- result handler (`_on_bg_result`) sends completion/failure/cancel message as a new Telegram message
- `/status` shows active background tasks via orchestrator status builder

## Limitations

- in-memory task registry only (no persistence across process restarts)
- no retry queue; each `/bg` submission is a single execution
