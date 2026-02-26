<p align="center">
  <img src="https://raw.githubusercontent.com/PleasePrompto/ductor/main/ductor_bot/bot/ductor_images/logo_text.png" alt="ductor" width="100%" />
</p>

<p align="center">
  <strong>Claude Code, Codex CLI, and Gemini CLI as your Telegram assistant.</strong><br>
  Persistent memory. Scheduled tasks. Live streaming. Docker sandboxing.<br>
  Uses only official CLIs. Nothing spoofed, nothing proxied.
</p>

<p align="center">
  <a href="https://pypi.org/project/ductor/"><img src="https://img.shields.io/pypi/v/ductor?color=blue" alt="PyPI" /></a>
  <a href="https://pypi.org/project/ductor/"><img src="https://img.shields.io/pypi/pyversions/ductor?v=1" alt="Python" /></a>
  <a href="https://github.com/PleasePrompto/ductor/blob/main/LICENSE"><img src="https://img.shields.io/github/license/PleasePrompto/ductor" alt="License" /></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &middot;
  <a href="#why-ductor">Why ductor?</a> &middot;
  <a href="#features">Features</a> &middot;
  <a href="#prerequisites">Prerequisites</a> &middot;
  <a href="#how-it-works">How it works</a> &middot;
  <a href="#telegram-bot-commands">Commands</a> &middot;
  <a href="docs/README.md">Docs</a> &middot;
  <a href="#contributing">Contributing</a>
</p>

---

ductor runs on your machine, uses your existing CLI authentication, and keeps state in plain JSON/Markdown under `~/.ductor/`.

You can:

- chat from Telegram with Claude/Codex/Gemini,
- stream responses live into edited Telegram messages,
- offload one-off tasks via `/bg` and receive a final result when they finish,
- run cron jobs and webhooks,
- let heartbeat checks proactively notify you,
- isolate runtime with Docker.

<p align="center">
  <img src="https://raw.githubusercontent.com/PleasePrompto/ductor/main/docs/images/ductor-start.jpeg" alt="ductor /start screen" width="49%" />
  <img src="https://raw.githubusercontent.com/PleasePrompto/ductor/main/docs/images/ductor-quick-actions.jpeg" alt="ductor quick action buttons" width="49%" />
</p>
<p align="center">
  <sub>Left: <code>/start</code> screen, right: quick action callbacks</sub>
</p>

## Quick start

```bash
pipx install ductor
ductor
```

The onboarding wizard handles CLI checks, Telegram setup, timezone, optional Docker, and optional background service install.

## Why ductor?

ductor executes the real provider CLIs as subprocesses. It does not proxy or spoof provider API calls.

- Official CLIs only (`claude`, `codex`, `gemini`)
- Rule files are plain Markdown (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`)
- Memory is one Markdown file (`workspace/memory_system/MAINMEMORY.md`)
- Automation state is JSON (`cron_jobs.json`, `webhooks.json`)

## Features

### Core

- Real-time streaming with live Telegram edits
- Provider/model switching with `/model` (provider sessions are preserved per chat)
- `@model` directive support (Claude + Gemini IDs/aliases)
- Media-aware input flow (text/media handling in Telegram layer)
- Inline callback buttons (`[button:Label]`)
- Queue tracking with per-message cancel while lock is held
- Telegram forum topic support (`message_thread_id` propagation)

### Automation

- Background tasks (`/bg`): push one-off tasks from the main chat into async execution while the chat stays responsive; receive a final completion message when done
- Cron jobs: in-process scheduler with timezone support, per-job overrides, quiet hours, dependency queue
- Webhooks: `wake` (inject into active chat) and `cron_task` (isolated task run) modes
- Heartbeat: proactive checks in active sessions with cooldown + quiet hours
- Cleanup: daily retention cleanup for `telegram_files/`, `output_to_user/`, and `api_files/`

### Infrastructure

- Built-in service manager:
  - Linux: `systemd --user`
  - macOS: `launchd` Launch Agent
  - Windows: Task Scheduler task
- Docker sidecar sandbox support (`Dockerfile.sandbox`)
- Configurable Docker host-directory mounts (`docker.mounts`, `ductor docker mount ...`)
- Restart protocol (`EXIT_RESTART = 42`) + sentinel-based user notifications
- Version/update flow with Telegram `/upgrade` callback path

### Developer UX

- Auto-onboarding on first run
- Deep-merge config upgrades (new keys auto-added)
- Rich diagnostics (`/diagnose`, `/status`)
- Interactive `/cron`, `/model`, `/showfiles` flows
- Cross-tool skill sync (`~/.ductor/workspace/skills`, `~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills`)

## Prerequisites

| Requirement | Details |
|---|---|
| Python 3.11+ | `python3 --version` |
| pipx (recommended) or pip | `pip install pipx` |
| At least one CLI installed + authenticated | Claude, Codex, or Gemini |
| Telegram Bot Token | from [@BotFather](https://t.me/BotFather) |
| Telegram User ID | from [@userinfobot](https://t.me/userinfobot) |
| Docker (optional) | recommended for sandboxing |

### CLI references:

- Claude Code CLI: https://docs.anthropic.com/en/docs/claude-code
- Codex CLI: https://github.com/openai/codex
- Gemini CLI: https://github.com/google-gemini/gemini-cli

Detailed installation: [`docs/installation.md`](docs/installation.md)

## Docker management

```bash
ductor docker enable
ductor docker disable
ductor docker rebuild
ductor docker mount /absolute/or/env/path
ductor docker unmount /absolute/or/env/path
ductor docker mounts
```

- `enable` / `disable`: toggles `docker.enabled` in `~/.ductor/config/config.json`
- `rebuild`: stops the bot, removes Docker container + image, rebuilds on next start
- `mount`: adds a host directory to `docker.mounts` (resolved absolute path; duplicates ignored)
- `unmount`: removes a configured mount (exact/resolved/basename match)
- `mounts`: shows configured host mounts and resolved container targets (`/mnt/<name>`)
- mount/unmount changes require restart or container rebuild to apply

## API management (beta)

```bash
ductor api enable
ductor api disable
```

- `enable`: writes/updates `config.api`, generates token if missing
- `disable`: sets `config.api.enabled=false`
- restart required after changes

## Run as a background service

```bash
ductor service install
ductor service status
ductor service start
ductor service stop
ductor service logs
ductor service uninstall
```

Platform details:

- Linux: `~/.config/systemd/user/ductor.service`, logs via `journalctl --user -u ductor -f`
- macOS: `~/Library/LaunchAgents/dev.ductor.plist`, log files at `~/.ductor/logs/service.log` + `service.err`, `ductor service logs` tails recent lines from `agent.log` (fallback newest `*.log`)
- Windows: Task Scheduler task `ductor` (10s logon delay, restart-on-failure retries), prefers `pythonw.exe -m ductor_bot`, `ductor service logs` tails recent lines from `agent.log` (fallback newest `*.log`)

## How it works

```text
You (Telegram)
  -> aiogram (AuthMiddleware + SequentialMiddleware)
  -> TelegramBot handlers
  -> Orchestrator (commands/directives/flows)
  -> CLIService
  -> claude/codex/gemini subprocess
  -> streamed or non-streamed response back to Telegram
```

Background systems run in the same process:

- periodic observers/watchers: `CronObserver`, `HeartbeatObserver`, `WebhookObserver`, `CleanupObserver`, `CodexCacheObserver`, `GeminiCacheObserver`, `UpdateObserver` (only for upgradeable installs), rule sync watcher, skill sync watcher
- on-demand subsystem: `BackgroundObserver` (`/bg` task runner)

Session behavior (important):

- sessions are chat-scoped,
- provider sessions are isolated per provider bucket,
- `/new` resets only the active provider bucket,
- switching back to another provider can resume that provider’s previous session.

## Workspace layout

```text
~/.ductor/
  config/config.json
  sessions.json
  cron_jobs.json
  webhooks.json
  CLAUDE.md
  AGENTS.md
  GEMINI.md
  logs/agent.log
  workspace/
    memory_system/MAINMEMORY.md
    cron_tasks/
    skills/
    tools/
      cron_tools/
      webhook_tools/
      telegram_tools/
      user_tools/
    telegram_files/
    output_to_user/
    api_files/
```

`workspace/api_files/` is created lazily on first API upload.

## Configuration

Config file: `~/.ductor/config/config.json`

Core keys:

| Key | Purpose |
|---|---|
| `provider` / `model` | default runtime target |
| `telegram_token`, `allowed_user_ids` | Telegram auth + allowlist |
| `cli_timeout`, `permission_mode`, `file_access` | runtime execution behavior |
| `reasoning_effort` | default Codex reasoning level |
| `gemini_api_key` | config fallback key for Gemini API-key mode |
| `docker.*` | sandbox settings |
| `heartbeat.*` | proactive check settings |
| `cleanup.*` | daily cleanup settings |
| `webhooks.*` | webhook server settings |
| `cli_parameters.claude/codex/gemini` | provider-specific extra args |

Full schema: [`docs/config.md`](docs/config.md)

## Telegram bot commands

| Command | Description |
|---|---|
| `/start` | Welcome screen with quick actions |
| `/new` | Reset active provider session for this chat |
| `/stop` | Abort active run and drain queued messages |
| `/model` | Interactive model/provider selector |
| `/status` | Session/provider/auth status |
| `/memory` | Show persistent memory file |
| `/cron` | Interactive cron management |
| `/bg` | Run task in background (notification on completion) |
| `/showfiles` | Browse `~/.ductor/` |
| `/diagnose` | Runtime diagnostics + cache/log info |
| `/upgrade` | Check/apply update flow |
| `/restart` | Restart bot |
| `/info` | Version + links |
| `/help` | Command reference |

## CLI commands

| Command | Description |
|---|---|
| `ductor` | Start bot (auto-onboarding if needed) |
| `ductor onboarding` | Run setup wizard (smart reset if configured) |
| `ductor reset` | Alias for onboarding |
| `ductor status` | Runtime status panel |
| `ductor stop` | Stop bot + optional Docker container |
| `ductor restart` | Stop and re-exec bot |
| `ductor upgrade` | Upgrade package and restart (non-dev mode) |
| `ductor uninstall` | Remove bot data + uninstall package |
| `ductor docker <enable|disable|rebuild|mount|unmount|mounts>` | Docker mode/config + user mount management |
| `ductor api <enable|disable>` | Direct WebSocket API toggle (beta) |
| `ductor service ...` | Service management (`install/status/start/stop/logs/uninstall`) |
| `ductor help` | Help + status |

## Documentation

- [`docs/README.md`](docs/README.md)
- [`docs/developer_quickstart.md`](docs/developer_quickstart.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/config.md`](docs/config.md)
- [`docs/automation.md`](docs/automation.md)
- [`docs/modules/`](docs/modules)

## Disclaimer

ductor runs official provider CLIs and does not impersonate provider clients. Terms and policies can change; validate your own compliance requirements before unattended automation.

Provider policy links:

- Anthropic: https://www.anthropic.com/policies/terms
- OpenAI: https://openai.com/policies/terms-of-use
- Google: https://policies.google.com/terms

## Contributing

```bash
git clone https://github.com/PleasePrompto/ductor.git
cd ductor
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff format .
ruff check .
mypy ductor_bot
```

Target quality bar: zero warnings, zero errors.

## License

[MIT](LICENSE)
