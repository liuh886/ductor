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
DISABLED_RUNTIME_FEATURES = (
    ("heartbeat", "enabled"),
    ("memory_context", "enabled"),
    ("memory_flush", "enabled"),
    ("memory_reflection", "enabled"),
    ("memory_compaction", "enabled"),
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

    vault_index = home / "workspace" / "memory_system" / "vault_index.db"
    results.append(CheckResult(vault_index.is_file(), "vault index", str(vault_index)))
    return results


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


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
        results.extend(runtime_identity_checks(repo, home))
    print(render(results))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
