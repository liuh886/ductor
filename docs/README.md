# ductor Docs

ductor is a Telegram bot that forwards chat input to the official Claude Code CLI, OpenAI Codex CLI, or Google Gemini CLI, streams replies back to Telegram, persists sessions in JSON, and runs cron/heartbeat/webhook automation plus daily cleanup in-process.

## Onboarding (Read in This Order)

1. `docs/developer_quickstart.md` -- fastest path for contributors and junior devs.
2. `docs/modules/setup_wizard.md` -- CLI commands, onboarding flow, upgrade flow.
3. `docs/architecture.md` -- startup, routing, streaming, callbacks, background systems.
4. `docs/config.md` -- config schema, merge behavior, provider/model resolution.
5. `docs/modules/orchestrator.md` -- routing core and flow behavior.
6. `docs/modules/bot.md` -- Telegram ingress, middleware, streaming UX, callbacks.
7. `docs/modules/cli.md` -- provider wrappers, stream parsing, process control.
8. `docs/modules/workspace.md` -- `~/.ductor` seeding, rule deployment/sync, runtime notices.
9. Remaining module docs (`session`, `cron`, `webhook`, `heartbeat`, `cleanup`, `infra`, `security`, `logging`, `skill_system`).

## System in 60 Seconds

- `ductor_bot/bot/`: aiogram handlers, auth/sequencing middleware, streaming editors, rich sender, file browser.
- `ductor_bot/orchestrator/`: command dispatch, directives/hooks, normal + heartbeat flows, observer wiring.
- `ductor_bot/cli/`: Claude/Codex/Gemini wrappers, stream-event normalization, process registry, auth detection, model caches.
- `ductor_bot/session/`: per-chat session lifecycle with provider-isolated buckets in `sessions.json`.
- `ductor_bot/cron/`: in-process scheduler for `cron_jobs.json` with task overrides, quiet hours, dependency queue.
- `ductor_bot/webhook/`: HTTP ingress (`/hooks/{hook_id}`) with `bearer`/`hmac`, `wake`/`cron_task`, and shared dependency queue.
- `ductor_bot/heartbeat/`: periodic proactive checks in active sessions.
- `ductor_bot/cleanup/`: daily retention cleanup for `telegram_files` + `output_to_user`.
- `ductor_bot/workspace/`: path resolution, home seeding from `_home_defaults`, RULES variant deployment, rule sync, skill sync.
- `ductor_bot/infra/`: PID lock, restart/update sentinels, Docker manager, service backends (Linux/macOS/Windows), updater/version checks.

Runtime behavior note:

- Normal CLI errors do not auto-reset sessions. Session context is preserved; users can retry or run `/new`.

## Documentation Index

- [Architecture](architecture.md)
- [Installation](installation.md)
- [Automation Quickstart](automation.md)
- [Developer Quickstart](developer_quickstart.md)
- [Configuration](config.md)
- Module docs:
  - [setup_wizard](modules/setup_wizard.md)
  - [bot](modules/bot.md)
  - [cli](modules/cli.md)
  - [orchestrator](modules/orchestrator.md)
  - [workspace](modules/workspace.md)
  - [skill_system](modules/skill_system.md)
  - [session](modules/session.md)
  - [cron](modules/cron.md)
  - [heartbeat](modules/heartbeat.md)
  - [webhook](modules/webhook.md)
  - [cleanup](modules/cleanup.md)
  - [security](modules/security.md)
  - [infra](modules/infra.md)
  - [logging](modules/logging.md)
