# Skill System

Cross-tool skill sync between ductor workspace and installed CLI homes.

## Files

- `workspace/skill_sync.py`: discovery, canonical resolution, sync, bundled skill sync, cleanup, watcher
- `workspace/init.py`: calls bundled + one-shot sync at startup
- `orchestrator/core.py`: starts watcher and runs shutdown cleanup
- `workspace/paths.py`: skill-related path properties

## Sync directories

```text
~/.ductor/workspace/skills/
<-> ~/.claude/skills/
<-> ~/.codex/skills/   (or $CODEX_HOME/skills)
<-> ~/.gemini/skills/
```

Only existing CLI home directories are included.

## Bundled skills

Bundled source: `ductor_bot/_home_defaults/workspace/skills/`.

`sync_bundled_skills(paths)` mirrors bundled skills into `~/.ductor/workspace/skills/`.

- normal mode: symlink/junction to bundled source
- Docker mode (`docker_active=True`): managed directory copy

Real user directories are never overwritten.

## Sync algorithm (`sync_skills`)

1. discover skills in each directory (subdirectories/symlinks only)
2. union all skill names
3. choose canonical source by priority: `ductor > claude > codex > gemini`
4. mirror canonical skill into other directories
5. clean broken symlinks

## Docker mode behavior

When `docker_active=True`, sync uses copies instead of links:

- copied dirs get marker `.ductor_managed`
- unchanged sources skip recopy via recursive mtime check
- unmanaged real directories are untouched

Reason: absolute host symlink targets may not resolve inside container mount namespace.

## External symlink protection

In link mode, existing symlinks pointing outside sync roots are treated as user-managed and left untouched.

## Cleanup on shutdown

`cleanup_ductor_links(paths)` removes only ductor-managed symlinks from CLI skill dirs.

Managed roots:

- `~/.ductor/workspace/skills`
- bundled skills directory

User-managed links/directories are preserved.

## Watcher

`watch_skill_sync(paths, docker_active=False, interval=30s)` runs as background task and calls `sync_skills` in worker thread.

## Safety guarantees

- real directories are never deleted/overwritten
- broken symlinks are cleaned
- hidden/internal directories are skipped (`.system`, `.claude`, `.git`, `.venv`, etc.)
- sync logic is cross-platform (Windows junction fallback)
