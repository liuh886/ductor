"""Persist the identity of the checkout serving the current runtime."""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ductor_bot.infra.json_store import atomic_json_save
from ductor_bot.infra.platform import CREATION_FLAGS

logger = logging.getLogger(__name__)

GIT_HEAD_TIMEOUT_SECONDS = 5.0


def current_git_head(repo: Path) -> str:
    """Return the current git HEAD, or an empty string when unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_HEAD_TIMEOUT_SECONDS,
            creationflags=CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def build_runtime_identity(*, framework_root: Path, pid: int | None = None) -> dict[str, Any]:
    """Build the serializable runtime identity payload."""
    root = framework_root.resolve()
    return {
        "framework_root": str(root),
        "git_head": current_git_head(root),
        "pid": os.getpid() if pid is None else pid,
        "started_at": datetime.now(UTC).isoformat(),
    }


def write_runtime_identity(path: Path, *, framework_root: Path) -> bool:
    """Write runtime identity without making maintenance metadata a startup dependency."""
    try:
        atomic_json_save(path, build_runtime_identity(framework_root=framework_root))
    except OSError:
        logger.warning("Could not persist runtime identity at %s", path, exc_info=True)
        return False
    return True
