# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.15.0] - 2025-03-24

### Added
- **Multi-transport architecture**: Refactored the core to support multiple chat protocols beyond Telegram.
- **Matrix support**: Official `matrix-nio` integration for running the agent in Matrix rooms.
- **WebSocket API**: Optional authenticated WebSocket API for direct agent interaction.
- **Message Bus**: Unified `MessageBus` for cross-transport async delivery.
- **Multi-agent supervision**: Support for internal localhost API bridge and shared knowledge sync between agents.
- **Matrix reaction buttons**: Native Matrix interaction support.
- **Service backends**: Improved platform-specific background service management (systemd, launchd, Task Scheduler).

### Changed
- **Directory restructure**: Moved transport-specific logic to `messenger/telegram/` and `messenger/matrix/`.
- **Session identification**: Introduced `SessionKey` to uniquely identify sessions across different transports.
- **CLI improvements**: Expanded `ductor` CLI with `service`, `docker`, `api`, `agents`, and `install` command groups.
- **Runtime paths**: Unified all runtime path logic in `workspace/paths.py`.

### Fixed
- Improved Docker sandboxing and managed file sync in Docker mode.
- Enhanced streaming delivery and fallback mechanisms.

## [0.14.0] - 2024-03-10

### Added
- Initial support for named background sessions (`/session`).
- Basic cron and webhook observers.
- Rule sync for `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md`.

## [0.1.0] - 2023-10-01
- Initial release as a Telegram-only bot for Claude CLI.
