# cli/

Provider-agnostic CLI execution layer for Claude Code, Codex, and Gemini.

## Files

- `types.py`: `AgentRequest`, `AgentResponse`, `CLIResponse`
- `base.py`: `BaseCLI`, `CLIConfig`, `docker_wrap()`, Windows helpers
- `factory.py`: provider factory (`claude` / `codex` / `gemini`)
- `service.py`: `CLIService` gateway for orchestrator
- `claude_provider.py`: Claude subprocess wrapper
- `codex_provider.py`: Codex subprocess wrapper
- `gemini_provider.py`: Gemini subprocess wrapper
- `stream_events.py`: normalized stream events + Claude stream parser
- `codex_events.py`: Codex JSONL parser
- `gemini_events.py`: Gemini NDJSON + JSON parser
- `coalescer.py`: streaming text coalescing buffer used by bot streaming dispatch
- `gemini_utils.py`: Gemini CLI discovery, trusted folder, model discovery helpers
- `codex_discovery.py`: Codex model discovery via `codex app-server` JSON-RPC
- `process_registry.py`: subprocess tracking/abort/kill
- `auth.py`: provider auth detection
- `param_resolver.py`: task override resolution for cron/webhook one-shot runs
- `codex_cache.py`, `codex_cache_observer.py`: Codex model cache + observer
- `gemini_cache.py`, `gemini_cache_observer.py`: Gemini model cache + observer

## Execution path

1. Orchestrator builds `AgentRequest`.
2. `CLIService._make_cli()` resolves model/provider.
3. `CLIServiceConfig` injects provider-specific global CLI args.
4. `create_cli()` selects provider wrapper.
5. provider executes subprocess and returns `CLIResponse`.
6. service converts to `AgentResponse`.

## Main-chat CLI parameters

Configured globally in `config.json`:

- `cli_parameters.claude`
- `cli_parameters.codex`
- `cli_parameters.gemini`

`CLIService` forwards them per provider.

## Task execution resolution (`param_resolver.py`)

Used by cron and webhook `cron_task` runs.

- input: `TaskOverrides(provider, model, reasoning_effort, cli_parameters)`
- output: immutable `TaskExecutionConfig`
- validation:
  - Claude model in `haiku|sonnet|opus`
  - Codex model validated against `CodexModelCache`
  - Gemini model validated against aliases/discovered IDs or `gemini-*` patterns
- Codex reasoning effort applied only when supported by model
- task `cli_parameters` are task-level only (no merge with global provider args)

## Streaming model

Normalized events in `stream_events.py` include:

- `AssistantTextDelta`
- `ToolUseEvent`
- `ToolResultEvent`
- `ThinkingEvent`
- `SystemStatusEvent`
- `CompactBoundaryEvent`
- `SystemInitEvent`
- `ResultEvent`

`CLIService.execute_streaming()` behavior:

- routes deltas/events to callbacks,
- checks `ProcessRegistry.was_aborted(chat_id)` on each event,
- if stream fails or lacks final result event:
  - aborted -> empty result,
  - non-error with accumulated text -> use accumulated text,
  - else retry non-streaming and mark `stream_fallback=True`.

`bot/message_dispatch.py` wraps delta delivery with `StreamCoalescer` (`coalescer.py`) so Telegram edits flush at readable boundaries (paragraph/sentence/idle/full flush).

SIGKILL retry is orchestrator-managed (`flows._recover_after_sigkill`), not CLIService-managed.

## Provider specifics

### Claude

- non-streaming uses `--output-format json`
- streaming uses `--output-format stream-json`
- respects `--max-turns`, `--max-budget-usd`, session resume/continue

### Codex

- uses `codex exec --json --color never --skip-git-repo-check`
- sandbox/approval flag selection from `permission_mode`
- reasoning effort via `-c model_reasoning_effort=...`
- `continue_session=True` is ignored for Codex

### Gemini

- command via `gemini` (or `node <index.js>` when resolved)
- non-streaming `--output-format json`, streaming `--output-format stream-json`
- permission bypass maps to `--approval-mode yolo`
- always includes `--include-directories .`
- trusts workspace path in `~/.gemini/trustedFolders.json`
- may inject `GEMINI_API_KEY` from ductor config when Gemini settings indicate API-key mode and no env key is set

## Auth detection (`auth.py`)

Statuses: `AUTHENTICATED`, `INSTALLED`, `NOT_FOUND`.

- Claude: `~/.claude/.credentials.json`
- Codex: `$CODEX_HOME/auth.json` (fallback install marker: `version.json`)
- Gemini:
  - CLI presence (`find_gemini_cli`)
  - OAuth creds (`~/.gemini/oauth_creds.json`)
  - env/.env/API-key/Vertex markers
  - `settings.json` selected auth mode
  - optional fallback to `~/.ductor/config/config.json` `gemini_api_key`

## Model caches

### Codex cache

- file: `~/.ductor/config/codex_models.json`
- discovery source: `discover_codex_models()` (`codex_discovery.py`) via `codex app-server` (`initialize` + `model/list`)
- loaded on startup with force refresh
- hourly refresh loop

### Gemini cache

- file: `~/.ductor/config/gemini_models.json`
- loaded on startup (uses cache when fresh, refreshes when stale/missing)
- hourly refresh loop
- refresh callback updates runtime Gemini model registry (`set_gemini_models`)

## Process registry

`ProcessRegistry` provides:

- registration/unregistration by chat
- abort markers (`was_aborted`, `clear_abort`)
- `kill_all(chat_id)`
- stale wall-clock cleanup (`kill_stale`)

Windows uses process-tree termination (`taskkill /F /T`) to avoid orphaned child processes.

## Docker wrapping

`docker_wrap(cmd, container, chat_id, working_dir)`:

- no container: execute with local cwd
- container: `docker exec -e DUCTOR_CHAT_ID=<id> <container> ...`, cwd unset
