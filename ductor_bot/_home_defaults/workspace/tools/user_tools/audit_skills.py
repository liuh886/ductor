"""Audit skill roots and emit a compact governance report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ductor_bot.workspace import audit_skill_sync, resolve_paths


def main() -> None:
    """Run the skill audit against the current ductor home."""
    parser = argparse.ArgumentParser(description="Audit ductor/codex/agent skill roots.")
    parser.add_argument(
        "--ductor-home",
        type=Path,
        default=None,
        help="Override the ductor home directory. Defaults to $DUCTOR_HOME or ~/.ductor.",
    )
    parser.add_argument(
        "--no-agent-roots",
        action="store_true",
        help="Skip ~/.ductor/agents/*/workspace/skills roots.",
    )
    args = parser.parse_args()

    paths = resolve_paths(ductor_home=args.ductor_home) if args.ductor_home else resolve_paths()
    report = audit_skill_sync(paths, include_agent_roots=not args.no_agent_roots)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
