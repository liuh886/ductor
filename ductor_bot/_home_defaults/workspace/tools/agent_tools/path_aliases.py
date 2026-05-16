#!/usr/bin/env python3
"""Manage the shared path alias registry."""

from __future__ import annotations

import argparse

from ductor_bot.workspace.path_aliases import AliasRegistration, PathAliasRegistry
from ductor_bot.workspace.paths import resolve_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Ductor path aliases")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List registered aliases")
    group.add_argument("--get", metavar="ALIAS", help="Show one alias")
    group.add_argument("--set", metavar="ALIAS", help="Create or update an alias")
    parser.add_argument("--path", dest="target_path", help="Canonical path for --set")
    parser.add_argument("--purpose", default="", help="Optional purpose/description for --set")
    args = parser.parse_args()

    paths = resolve_paths()
    registry = PathAliasRegistry(paths)

    if args.list:
        aliases = registry.load()
        if not aliases:
            print("No path aliases registered.")
            return
        for alias, entry in sorted(aliases.items()):
            suffix = f" | {entry.purpose}" if entry.purpose else ""
            print(f"@{alias} -> {entry.path}{suffix}")
        return

    if args.get:
        entry = registry.get(args.get)
        if entry is None:
            print(f"Alias @{args.get.lstrip('@')} not found.")
            raise SystemExit(1)
        suffix = f" | {entry.purpose}" if entry.purpose else ""
        print(f"@{entry.alias} -> {entry.path}{suffix}")
        return

    if not args.target_path:
        parser.error("--set requires --path")

    entry = registry.upsert(
        AliasRegistration(
            alias=args.set.lstrip("@"),
            path=args.target_path,
            purpose=args.purpose,
        )
    )
    suffix = f" | {entry.purpose}" if entry.purpose else ""
    print(f"Saved @{entry.alias} -> {entry.path}{suffix}")


if __name__ == "__main__":
    main()
