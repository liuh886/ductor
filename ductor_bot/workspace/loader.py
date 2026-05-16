"""Workspace file reader: safe reads with fallback defaults."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from ductor_bot.runtime.memory import MemoryFragment, extract_markdown_fragments
from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)


def read_file(path: Path) -> str | None:
    """Read a file, returning None if it does not exist or cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("Failed to read file: %s", path, exc_info=True)
        return None


def _extract_task_context(tasks_text: str) -> list[str]:
    """Extract context keywords from active tasks in TASKS.md."""
    if not tasks_text:
        return []
    keywords: set[str] = set()
    for raw_line in tasks_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(("- [ ]", "* [ ]")):
            words = [w.strip(".,;:()[]{}") for w in stripped[5:].split() if len(w) > 3]
            keywords.update(w.lower() for w in words)
    return list(keywords)


def read_mainmemory(
    paths: DuctorPaths,
    *,
    fragment_repo: object | None = None,
    agent_name: str = "",
) -> str:
    """Read MAINMEMORY.md, preferring fragment-backed context when available."""
    _sync_memory_fragments(
        paths,
        fragment_repo,
        agent_name=agent_name,
    )

    tasks_text = load_tasks(paths)
    context_keywords = _extract_task_context(tasks_text)

    fragment_text = _read_fragment_context(
        fragment_repo,
        scope="mainmemory",
        agent_name=agent_name,
        context_keywords=context_keywords,
    )
    shared_text = _read_fragment_context(
        fragment_repo,
        scope="sharedmemory",
        context_keywords=context_keywords,
    )
    rendered = "\n\n".join(part for part in (fragment_text, shared_text) if part.strip())
    if rendered.strip():
        return rendered
    return read_file(paths.mainmemory_path) or ""


def load_soul(paths: DuctorPaths) -> str:
    """Read SOUL.md from the workspace."""
    return read_file(paths.soul_path) or ""


def load_tasks(paths: DuctorPaths) -> str:
    """Read TASKS.md from the migrated lifeos_agency/agent location."""
    return read_file(paths.shared_tasks_path) or ""


def load_task_state(
    paths: DuctorPaths,
    *,
    storage_key: str,
    task_state_repo: object | None = None,
) -> str:
    """Return task context, preferring persisted ``task_states`` over ``TASKS.md``."""
    if task_state_repo is not None:
        list_by_storage_key = getattr(task_state_repo, "list_by_storage_key", None)
        if callable(list_by_storage_key):
            try:
                rows = list_by_storage_key(storage_key)
            except Exception:
                logger.debug("Failed to load task_states for %s", storage_key, exc_info=True)
            else:
                rendered = _render_task_states(rows)
                if rendered.strip():
                    return rendered
    return load_tasks(paths)


def _sync_memory_fragments(
    paths: DuctorPaths,
    fragment_repo: object | None,
    *,
    agent_name: str = "",
) -> None:
    """Extract Markdown memory files into the runtime fragment store when available."""
    if fragment_repo is None:
        return
    replace_for_scope = getattr(fragment_repo, "replace_for_scope", None)
    list_by_scope = getattr(fragment_repo, "list_by_scope", None)
    if not callable(replace_for_scope):
        return
    if not callable(list_by_scope):
        return

    try:
        existing_main = list_by_scope("mainmemory", agent_name=agent_name) if agent_name else []
        existing_shared = list_by_scope("sharedmemory")
    except Exception:
        logger.debug("Failed to inspect existing memory fragments", exc_info=True)
        return

    try:
        if agent_name:
            main_text = read_file(paths.mainmemory_path) or ""
            display_path = "@ductor/workspace/memory_system/MAINMEMORY.md"
            main_fragments = (
                _stamp_fragments_from_source(
                    extract_markdown_fragments(
                        main_text,
                        source_path=display_path,
                        source_kind="mainmemory",
                        scope="mainmemory",
                        agent_name=agent_name,
                    ),
                    paths.mainmemory_path,
                )
                if main_text.strip()
                else []
            )
            if _should_refresh_scope(paths.mainmemory_path, existing_main) and not _fragments_match_existing(
                existing_main,
                main_fragments,
            ):
                replace_for_scope("mainmemory", main_fragments, agent_name=agent_name)

        shared_text = read_file(paths.sharedmemory_path) or ""
        display_path = "@ductor/SHAREDMEMORY.md"
        shared_fragments = (
            _stamp_fragments_from_source(
                extract_markdown_fragments(
                    shared_text,
                    source_path=display_path,
                    source_kind="sharedmemory",
                    scope="sharedmemory",
                    agent_name="",
                ),
                paths.sharedmemory_path,
            )
            if shared_text.strip()
            else []
        )
        if _should_refresh_scope(paths.sharedmemory_path, existing_shared) and not _fragments_match_existing(
            existing_shared,
            shared_fragments,
        ):
            replace_for_scope("sharedmemory", shared_fragments)
    except Exception:
        logger.debug("Failed to sync memory fragments", exc_info=True)


def _render_task_states(rows: list[dict[str, object]]) -> str:
    """Render task-state rows into a compact prompt-friendly summary."""
    active_rows = [
        row
        for row in rows
        if str(row.get("status", "")).strip().upper() not in {"DONE", "FAILED", "CANCELLED"}
    ]
    if not active_rows:
        return ""
    lines = ["# Active Task State"]
    for row in active_rows:
        task_id = str(row.get("task_id", "")).strip() or "<unknown>"
        status = str(row.get("status", "PENDING")).strip() or "PENDING"
        step_label = str(row.get("step_label", "")).strip()
        current_step = row.get("current_step")
        total_steps = row.get("total_steps")
        line = f"- {task_id}: {status}"
        if step_label:
            line += f" | {step_label}"
        if isinstance(current_step, int):
            if isinstance(total_steps, int) and total_steps > 0:
                line += f" | step {current_step}/{total_steps}"
            else:
                line += f" | step {current_step}"
        lines.append(line)

        snapshot = row.get("context_snapshot_json", {})
        rendered_snapshot = _render_task_snapshot(snapshot)
        if rendered_snapshot:
            lines.extend(f"  {part}" for part in rendered_snapshot.splitlines())
    return "\n".join(lines)


def _render_task_snapshot(snapshot: object) -> str:
    """Render a task-state context snapshot into concise bullet lines."""
    if isinstance(snapshot, dict):
        parts: list[str] = []
        for key, value in snapshot.items():
            if value in ("", None, [], {}):
                continue
            if isinstance(value, (dict, list)):
                encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
                parts.append(f"- {key}: {encoded}")
            else:
                parts.append(f"- {key}: {value}")
        return "\n".join(parts[:8])
    return ""


def _should_refresh_scope(path: Path, existing_rows: list[dict[str, object]]) -> bool:
    """Return True when source Markdown should refresh the fragment rows."""
    if not existing_rows:
        return True
    try:
        file_mtime = path.stat().st_mtime
    except FileNotFoundError:
        return True
    except OSError:
        logger.debug("Failed to stat memory source %s", path, exc_info=True)
        return False

    latest_row_update = max(_row_timestamp(row) for row in existing_rows)
    # Prefer persisted fragment rows on timestamp ties; otherwise immediate
    # file writes and subsequent fragment updates can race on coarse mtime
    # precision (observed on Windows temp files).
    return file_mtime > latest_row_update


def _fragment_signature(fragment: MemoryFragment) -> tuple[object, ...]:
    """Return the stable identity of an extracted fragment, excluding generated IDs."""
    return (
        fragment.title,
        fragment.body,
        fragment.source_path,
        fragment.source_kind,
        fragment.scope,
        fragment.agent_name,
        tuple(fragment.tags),
        fragment.importance,
    )


def _row_signature(row: dict[str, object]) -> tuple[object, ...]:
    """Return the stable identity of a persisted fragment row, excluding mutable metadata."""
    raw_tags = row.get("tags_json", [])
    tags = raw_tags if isinstance(raw_tags, list) else []
    return (
        str(row.get("title", "")),
        str(row.get("body", "")),
        str(row.get("source_path", "")),
        str(row.get("source_kind", "")),
        str(row.get("scope", "")),
        str(row.get("agent_name", "")),
        tuple(str(tag) for tag in tags),
        row.get("importance", 0.0),
    )


def _fragments_match_existing(
    existing_rows: list[dict[str, object]],
    new_fragments: list[MemoryFragment],
) -> bool:
    """Return True when the persisted fragments already match the extracted content."""
    if len(existing_rows) != len(new_fragments):
        return False
    existing_signatures = [_row_signature(row) for row in existing_rows]
    new_signatures = [_fragment_signature(fragment) for fragment in new_fragments]
    return existing_signatures == new_signatures


def _row_timestamp(row: dict[str, object]) -> float:
    """Return the best-effort update timestamp for a persisted fragment row."""
    for key in ("updated_at", "created_at"):
        value = row.get(key)
        if isinstance(value, (int, float, str)):
            try:
                return float(value)
            except ValueError:
                continue
        try:
            return float(str(value))
        except (TypeError, ValueError):
            continue
    return 0.0


def _stamp_fragments_from_source(
    fragments: list[MemoryFragment],
    source_path: Path,
) -> list[MemoryFragment]:
    """Stamp extracted fragments with the source file mtime when available."""
    try:
        source_mtime = source_path.stat().st_mtime
    except FileNotFoundError:
        return fragments
    except OSError:
        logger.debug("Failed to stat source for fragment stamping: %s", source_path, exc_info=True)
        return fragments

    for fragment in fragments:
        fragment.created_at = source_mtime
        fragment.updated_at = source_mtime
    return fragments


def _read_fragment_context(
    fragment_repo: object | None,
    *,
    scope: str,
    agent_name: str = "",
    context_keywords: list[str] | None = None,
) -> str:
    """Return rendered memory fragments from a runtime repository if available."""
    if fragment_repo is None:
        return ""
    list_by_scope = getattr(fragment_repo, "list_by_scope", None)
    if not callable(list_by_scope):
        return ""

    rows: list[dict[str, object]] = []
    try:
        rows = list_by_scope(scope, agent_name=agent_name) if agent_name else list_by_scope(scope)
    except Exception:
        logger.debug("Failed to read memory fragments", exc_info=True)
        return ""

    if context_keywords:
        filtered_rows = []
        for row in rows:
            text = (str(row.get("title", "")) + " " + str(row.get("body", ""))).lower()
            score = sum(1 for kw in context_keywords if kw in text)
            filtered_rows.append((score, row))
        # Keep top 10 fragments with a score > 0, or fallback to top 5 recent if none match well
        filtered_rows.sort(key=lambda x: x[0], reverse=True)
        filtered_results = [r[1] for r in filtered_rows if r[0] > 0][:10]
        if not filtered_results:
            # Fallback if no keyword match
            filtered_results = [r[1] for r in filtered_rows][:5]
        rows = filtered_results

    return _render_fragments(rows)


def _render_fragments(fragments: Iterable[dict[str, object]]) -> str:
    """Render fragment rows into a deterministic Markdown block."""
    parts: list[str] = []
    for fragment in fragments:
        body = str(fragment.get("body", "")).strip()
        if not body:
            continue
        title = str(fragment.get("title", "")).strip()
        source_path = str(fragment.get("source_path", "")).strip()
        ulid = str(fragment.get("ulid", "")).strip()

        lines: list[str] = []
        if title:
            if ulid:
                lines.append(f"## [ID: {ulid}] {title}")
            else:
                lines.append(f"## {title}")
        elif ulid:
            lines.append(f"[ID: {ulid}]")

        lines.append(body)
        if source_path:
            lines.append(f"_Source: {source_path}_")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
