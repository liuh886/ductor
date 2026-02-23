# infra/

Runtime infrastructure: process lifecycle, restart/update flow, Docker sandbox, service backends.

## Files

- `pidlock.py`: single-instance PID lock
- `restart.py`: restart marker/sentinel helpers, `EXIT_RESTART = 42`
- `docker.py`: `DockerManager`
- `install.py`: install mode detection (`pipx` / `pip` / `dev`)
- `service.py`: platform dispatch facade
- `service_common.py`: shared console helper
- `service_logs.py`: shared recent-log renderer
- `service_linux.py`: Linux systemd backend
- `service_macos.py`: macOS launchd backend
- `service_windows.py`: Windows Task Scheduler backend
- `version.py`: PyPI version/changelog utilities
- `updater.py`: `UpdateObserver`, upgrade helpers/sentinel
- `ductor_bot/run.py`: supervisor loop

## Service management

`service.py` dispatches by platform:

- Linux -> systemd user service (`service_linux.py`)
- macOS -> launchd Launch Agent (`service_macos.py`)
- Windows -> Task Scheduler (`service_windows.py`)

Shared helpers:

- `ensure_console()` in `service_common.py`
- `print_recent_logs()` in `service_logs.py`

`print_recent_logs()` behavior:

- prefers `~/.ductor/logs/agent.log`
- fallback: newest `*.log`
- prints last 50 lines by default

### Linux backend

- service file: `~/.config/systemd/user/ductor.service`
- optional linger enable via `sudo loginctl enable-linger <user>`
- logs command uses `journalctl --user -u ductor -f`

### macOS backend

- plist: `~/Library/LaunchAgents/dev.ductor.plist`
- launchd logs configured to `service.log` / `service.err`
- `ductor service logs` uses `print_recent_logs()` over ductor log files

### Windows backend

- scheduled task name: `ductor`
- starts 10s after logon
- prefers `pythonw.exe -m ductor_bot`, fallback `ductor` binary
- explicit admin hint panel on access-denied `schtasks` errors
- `ductor service logs` uses `print_recent_logs()`

## PID lock

`acquire_lock(pid_file, kill_existing=True)` is used for bot startup.

- detects stale/alive PID
- optionally terminates existing process
- writes current PID

Windows compatibility includes broader `OSError` handling around PID liveness/termination checks.

## Restart protocol

- `/restart` or restart marker file triggers exit code `42`
- restart sentinel stores chat + message for post-restart notification
- sentinel consumed on next startup

## Docker manager

`DockerManager.setup()`:

1. verify Docker binary/daemon
2. ensure image (build when missing and `auto_build=true`)
3. reuse running container or start new one
4. mount `~/.ductor -> /ductor`
5. mount provider homes when present:
   - `~/.claude`
   - `~/.codex`
   - `~/.gemini`

Linux adds UID/GID mapping (`--user uid:gid`) to avoid root-owned host files.

If setup fails, orchestrator falls back to host execution.

## Version/update system

- `check_pypi()` fetches latest package metadata
- `UpdateObserver` checks periodically and notifies once per new version
- `perform_upgrade()` runs `pipx upgrade --force ductor` (or pip fallback)
- upgrade sentinel stores old/new version + chat for post-restart confirmation

## Supervisor (`ductor_bot/run.py`)

Runs `python -m ductor_bot` child process.

Restart conditions:

- exit `42` -> immediate restart
- file change (when watch mode enabled) -> restart
- crash -> exponential backoff
