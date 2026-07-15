"""Read-only audit for the minimal local Ductor stack."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ductor_bot.infra.platform import CREATION_FLAGS

UPSTREAM_BASE = "upstream/main"
LOCAL_STACK_PREFIX = "codex/local-stack"
COMMAND_TIMEOUT_SECONDS = 20.0
RUNTIME_IDENTITY_FILENAME = "runtime_identity.json"
LEGACY_LINEAGE_FIELDS = frozenset(
    {
        "lineage_id",
        "lineage_root",
        "lineage_parent",
        "lineage_depth",
        "lineage_reason",
        "lineage_created_at",
    }
)
LEGACY_CONFIG_FIELDS = frozenset({"state_backend", "state_db_path"})
LEGACY_MEMORY_PATHS = (
    Path("SHAREDMEMORY.md"),
    Path("state.db"),
    Path("workspace/archive"),
    Path("workspace/cron_tasks/archives"),
    Path("workspace/tools/memory"),
    Path("workspace/tools/agent_tools/search_past_sessions.py"),
    Path("workspace/tools/agent_tools/edit_shared_knowledge.py"),
    Path("workspace/tools/agent_tools/memory_atomic_op.py"),
)
TASK_RULE_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "GEMINI.md"})
DISABLED_RUNTIME_FEATURES = (
    ("heartbeat", "enabled"),
    ("memory_context", "enabled"),
    ("memory_flush", "enabled"),
    ("memory_reflection", "enabled"),
    ("memory_compaction", "enabled"),
)
RUNTIME_FATAL_SIGNATURES = (
    "Context length exceeded",
    "An internal error occurred",
    "'ProcessRegistry' object has no attribute 'was_aborted_topic'",
    "Main agent crashed, terminating supervisor",
    "Main agent exceeded max transient retries",
)


@dataclass(frozen=True)
class CheckResult:
    """One audit result."""

    ok: bool
    label: str
    detail: str


def _run(repo: Path, *args: str) -> tuple[int, str]:
    """Run one bounded command and return its code and stdout."""
    try:
        result = subprocess.run(
            [*args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            creationflags=CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""
    return result.returncode, result.stdout.strip()


def _run_git(repo: Path, *args: str) -> tuple[int, str]:
    return _run(repo, "git", *args)


def _git_output(repo: Path, *args: str) -> str:
    code, output = _run_git(repo, *args)
    return output if code == 0 else ""


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _short_oid(value: object) -> str:
    return value[:8] if isinstance(value, str) and value else "<missing>"


def git_checks(repo: Path) -> list[CheckResult]:
    """Check branch shape and local/fork baseline alignment."""
    branch = _git_output(repo, "branch", "--show-current") or "<unknown>"
    status = _git_output(repo, "status", "--porcelain")
    head = _git_output(repo, "rev-parse", "HEAD")
    upstream = _git_output(repo, "rev-parse", UPSTREAM_BASE)
    main = _git_output(repo, "rev-parse", "main")
    origin_main = _git_output(repo, "rev-parse", "origin/main")
    ahead = _git_output(repo, "rev-list", "--count", f"{UPSTREAM_BASE}..HEAD")
    behind = _git_output(repo, "rev-list", "--count", f"HEAD..{UPSTREAM_BASE}")
    ancestor = _run_git(repo, "merge-base", "--is-ancestor", UPSTREAM_BASE, "HEAD")[0] == 0

    return [
        CheckResult(branch.startswith(LOCAL_STACK_PREFIX), "local-stack branch", branch),
        CheckResult(not status, "working tree", "clean" if not status else "has changes"),
        CheckResult(bool(head and upstream), "git refs", "HEAD and upstream/main resolved"),
        CheckResult(ancestor, "upstream ancestry", f"base={_short_oid(upstream)}"),
        CheckResult(behind == "0", "upstream drift", f"behind={behind or '?'}"),
        CheckResult(ahead.isdigit(), "local commit count", f"ahead={ahead or '?'}"),
        CheckResult(main == upstream, "local main baseline", f"main={_short_oid(main)}"),
        CheckResult(
            origin_main == upstream,
            "fork main baseline",
            f"origin={_short_oid(origin_main)} upstream={_short_oid(upstream)}",
        ),
    ]


def _config_value(config: dict[str, Any], section: str, field: str) -> object:
    section_value = config.get(section)
    if not isinstance(section_value, dict):
        return None
    return section_value.get(field)


def runtime_config_checks(home: Path) -> list[CheckResult]:
    """Check local cost controls and explicit vault retrieval state."""
    config_path = home / "config" / "config.json"
    config = _load_json(config_path)
    if config is None:
        return [
            CheckResult(
                ok=False, label="runtime config", detail=f"missing or invalid {config_path}"
            )
        ]

    results: list[CheckResult] = []
    for section, field in DISABLED_RUNTIME_FEATURES:
        value = _config_value(config, section, field)
        results.append(
            CheckResult(
                value is not True,
                f"{section}.{field}",
                "disabled/default" if value is not True else "enabled",
            )
        )

    prompt_files = config.get("append_system_prompt_files")
    prompt_files_disabled = prompt_files in (None, [])
    results.append(
        CheckResult(
            prompt_files_disabled,
            "append_system_prompt_files",
            "disabled/default" if prompt_files_disabled else f"configured={len(prompt_files)}",
        )
    )

    vault_index = home / "workspace" / "memory_system" / "vault_index.db"
    results.append(CheckResult(vault_index.is_file(), "vault index", str(vault_index)))
    return results


def _count_legacy_lineage(value: object) -> int:
    if isinstance(value, dict):
        return sum(key in LEGACY_LINEAGE_FIELDS for key in value) + sum(
            _count_legacy_lineage(item) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_count_legacy_lineage(item) for item in value)
    return 0


def _active_agent_homes(home: Path) -> tuple[list[Path], str | None]:
    """Return main + registered agent homes without following arbitrary paths."""
    homes = [home]
    agents_path = home / "agents.json"
    try:
        agents = json.loads(agents_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        agents = []
    except (OSError, json.JSONDecodeError):
        return homes, f"invalid {agents_path}"

    if not isinstance(agents, list):
        return homes, f"invalid {agents_path}"
    for agent in agents:
        if not isinstance(agent, dict) or not isinstance(agent.get("name"), str):
            return homes, f"invalid {agents_path}"
        name = agent["name"]
        if not name or Path(name).name != name:
            return homes, f"invalid agent name {name!r}"
        homes.append(home / "agents" / name)
    return homes, None


def context_isolation_checks(home: Path) -> list[CheckResult]:
    """Require low-context Codex settings in every active agent home."""
    agent_homes, error = _active_agent_homes(home)
    if error:
        return [CheckResult(ok=False, label="context isolation", detail=error)]

    sync_disabled = 0
    plugins_disabled = 0
    for agent_home in agent_homes:
        config = _load_json(agent_home / "config" / "config.json")
        if config is None:
            continue
        if _config_value(config, "skills", "sync_enabled") is False:
            sync_disabled += 1
        cli_parameters = _config_value(config, "cli_parameters", "codex")
        if isinstance(cli_parameters, list) and any(
            cli_parameters[index : index + 2] == ["--disable", "plugins"]
            for index in range(len(cli_parameters) - 1)
        ):
            plugins_disabled += 1

    total = len(agent_homes)
    return [
        CheckResult(
            sync_disabled == total,
            "skill sync isolation",
            f"disabled={sync_disabled} homes={total}",
        ),
        CheckResult(
            plugins_disabled == total,
            "codex plugin isolation",
            f"disabled={plugins_disabled} homes={total}",
        ),
    ]


def legacy_session_checks(home: Path) -> list[CheckResult]:
    """Reject obsolete P0 lineage fields in active agent session stores."""
    agent_homes, error = _active_agent_homes(home)
    if error:
        return [CheckResult(ok=False, label="legacy session lineage", detail=error)]

    total = 0
    scanned = 0
    for agent_home in agent_homes:
        path = agent_home / "sessions.json"
        if not path.is_file():
            continue
        payload = _load_json(path)
        if payload is None:
            return [CheckResult(ok=False, label="legacy session lineage", detail=f"invalid {path}")]
        total += _count_legacy_lineage(payload)
        scanned += 1
    return [CheckResult(total == 0, "legacy session lineage", f"fields={total} files={scanned}")]


def legacy_memory_surface_checks(home: Path) -> list[CheckResult]:
    """Reject retired LifeOS/AMEM runtime files and configuration keys."""
    agent_homes, error = _active_agent_homes(home)
    if error:
        return [CheckResult(ok=False, label="legacy memory surfaces", detail=error)]

    paths = [
        agent_home / relative for agent_home in agent_homes for relative in LEGACY_MEMORY_PATHS
    ]
    existing = [path for path in paths if path.exists() or path.is_symlink()]

    obsolete_keys = 0
    config_files = 0
    for agent_home in agent_homes:
        config_path = agent_home / "config" / "config.json"
        if not config_path.is_file():
            continue
        config = _load_json(config_path)
        if config is None:
            return [
                CheckResult(
                    ok=False,
                    label="legacy memory config",
                    detail=f"invalid {config_path}",
                )
            ]
        obsolete_keys += len(LEGACY_CONFIG_FIELDS.intersection(config))
        config_files += 1

    return [
        CheckResult(
            not existing,
            "legacy memory surfaces",
            f"paths={len(existing)} homes={len(agent_homes)}",
        ),
        CheckResult(
            obsolete_keys == 0,
            "legacy memory config",
            f"keys={obsolete_keys} files={config_files}",
        ),
    ]


def _registered_task_folders(home: Path, raw_tasks: list[object]) -> set[Path]:
    active_folders: set[Path] = set()
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id or Path(task_id).name != task_id:
            continue
        configured_dir = raw.get("tasks_dir")
        base = Path(configured_dir) if isinstance(configured_dir, str) and configured_dir else None
        active_folders.add((base or home / "workspace" / "tasks").joinpath(task_id).resolve())
    return active_folders


def _find_orphan_task_artifacts(agent_homes: list[Path], active_folders: set[Path]) -> list[Path]:
    artifacts: list[Path] = []
    for agent_home in agent_homes:
        tasks_root = agent_home / "workspace" / "tasks"
        if not tasks_root.is_dir():
            continue
        for child in tasks_root.iterdir():
            if child.is_dir() or child.is_symlink():
                if child.resolve() not in active_folders:
                    artifacts.append(child)
            elif child.name not in TASK_RULE_FILES:
                artifacts.append(child)
    return artifacts


def orphan_task_artifact_checks(home: Path) -> list[CheckResult]:
    """Reject task artifacts that are not referenced by the persistent registry."""
    agent_homes, error = _active_agent_homes(home)
    if error:
        return [CheckResult(ok=False, label="orphan task artifacts", detail=error)]

    registry = _load_json(home / "tasks.json")
    raw_tasks = registry.get("tasks") if registry is not None else None
    if not isinstance(raw_tasks, list):
        return [
            CheckResult(
                ok=False,
                label="orphan task artifacts",
                detail=f"invalid {home / 'tasks.json'}",
            )
        ]

    active_folders = _registered_task_folders(home, raw_tasks)
    artifacts = _find_orphan_task_artifacts(agent_homes, active_folders)
    return [
        CheckResult(
            not artifacts,
            "orphan task artifacts",
            f"artifacts={len(artifacts)} homes={len(agent_homes)}",
        )
    ]


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def runtime_log_checks(home: Path) -> list[CheckResult]:
    """Inspect only log records emitted by the currently locked process."""
    pid = _read_pid(home / "bot.pid")
    log_path = home / "logs" / "agent.log"
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [CheckResult(ok=False, label="runtime log", detail=f"missing {log_path}")]

    marker = f"PID lock acquired (pid={pid})" if pid is not None else ""
    if not marker or marker not in log_text:
        return [
            CheckResult(
                ok=False,
                label="runtime log",
                detail=f"current pid marker missing pid={pid!r}",
            )
        ]

    current_run = log_text.rsplit(marker, 1)[1]
    fatal_matches = sum(current_run.count(signature) for signature in RUNTIME_FATAL_SIGNATURES)
    network_events = current_run.count("TelegramNetworkError")
    return [
        CheckResult(
            fatal_matches == 0,
            "runtime fatal signatures",
            f"matches={fatal_matches}",
        ),
        CheckResult(
            ok=True,
            label="telegram network events",
            detail=f"events={network_events}",
        ),
    ]


def runtime_identity_checks(repo: Path, home: Path) -> list[CheckResult]:
    """Check that the process lock and runtime metadata identify this checkout."""
    identity_path = home / RUNTIME_IDENTITY_FILENAME
    identity = _load_json(identity_path)
    if identity is None:
        return [
            CheckResult(
                ok=False, label="runtime identity", detail=f"missing or invalid {identity_path}"
            )
        ]

    runtime_root = identity.get("framework_root")
    runtime_head = identity.get("git_head")
    runtime_pid = identity.get("pid")
    started_at = identity.get("started_at")
    checkout_head = _git_output(repo, "rev-parse", "HEAD")
    bot_pid = _read_pid(home / "bot.pid")
    try:
        same_root = isinstance(runtime_root, str) and Path(runtime_root).resolve() == repo.resolve()
    except OSError:
        same_root = False

    return [
        CheckResult(same_root, "runtime checkout", repr(runtime_root)),
        CheckResult(
            isinstance(runtime_head, str) and runtime_head == checkout_head,
            "runtime commit",
            f"runtime={_short_oid(runtime_head)} checkout={_short_oid(checkout_head)}",
        ),
        CheckResult(
            isinstance(runtime_pid, int) and runtime_pid == bot_pid,
            "runtime pid lock",
            f"identity={runtime_pid!r} lock={bot_pid!r}",
        ),
        CheckResult(
            isinstance(started_at, str) and bool(started_at),
            "runtime start time",
            repr(started_at),
        ),
    ]


def maintenance_file_checks(repo: Path) -> list[CheckResult]:
    """Check that the portable PM2 and maintenance surfaces are present."""
    ecosystem = repo / "ecosystem.config.js"
    maintenance = repo / "docs" / "local-stack-maintenance.md"
    fork_inventory = repo / "docs" / "local-stack-origin-main-inventory.md"
    ecosystem_text = ecosystem.read_text(encoding="utf-8") if ecosystem.is_file() else ""
    return [
        CheckResult(maintenance.is_file(), "maintenance guide", str(maintenance)),
        CheckResult(fork_inventory.is_file(), "fork main inventory", str(fork_inventory)),
        CheckResult("cwd: __dirname" in ecosystem_text, "portable PM2 cwd", str(ecosystem)),
        CheckResult("disable_logs: true" in ecosystem_text, "bounded PM2 logs", str(ecosystem)),
    ]


def render(results: list[CheckResult]) -> str:
    lines = ["Local-stack audit"]
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        lines.append(f"[{marker}] {result.label}: {result.detail}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--ductor-home", type=Path, default=Path("~/.ductor").expanduser())
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Skip config and running-checkout checks before the first deployment.",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    home = args.ductor_home.expanduser().resolve()
    results = [*git_checks(repo), *maintenance_file_checks(repo)]
    if not args.skip_runtime:
        results.extend(runtime_config_checks(home))
        results.extend(context_isolation_checks(home))
        results.extend(legacy_session_checks(home))
        results.extend(legacy_memory_surface_checks(home))
        results.extend(orphan_task_artifact_checks(home))
        results.extend(runtime_identity_checks(repo, home))
        results.extend(runtime_log_checks(home))
    print(render(results))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
