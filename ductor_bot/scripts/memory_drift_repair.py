"""Repair drift between MAINMEMORY.md files and state.db memory fragments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ductor_bot.multiagent.shared_knowledge import _sync_agent_io
from ductor_bot.runtime.memory import extract_markdown_fragments
from ductor_bot.runtime.memory.sync import reverse_sync_source
from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_fragment_repo import MemoryFragmentRepository


@dataclass(slots=True)
class RepairResult:
    agent_name: str
    action: str
    fragment_count: int
    path: Path


_ROLE_BOOTSTRAP_BY_AGENT: dict[str, dict[str, tuple[str, ...]]] = {
    "bot3-writer": {
        "about": (
            "- Role: drafting and polishing long-form writing for the user.",
            "- Default stance: optimize for clarity, structure, and publishable output quality.",
        ),
        "facts": (
            "- Preserve reusable tone, phrasing preferences, and audience/context constraints.",
            "- Track recurring writing deliverables, document types, and revision patterns.",
        ),
        "decisions": (
            "- Prefer concise, high-signal prose unless the task explicitly needs expansion.",
            "- Surface outline and narrative-structure improvements before line-level polish.",
        ),
    },
    "seismic-bot": {
        "about": (
            "- Role: geophysics company CEO and business lead.",
            "- Default stance: evaluate geophysics, seismic, and subsurface work through business value, delivery capability, and long-horizon positioning.",
        ),
        "facts": (
            "- Preserve durable customer patterns, project types, commercial opportunities, and capability gaps relevant to the business.",
            "- Track how technical strengths map into winning bids, trusted delivery, and repeatable revenue.",
        ),
        "decisions": (
            "- Prefer business judgment with clear tradeoffs over narrow technical commentary.",
            "- Route deep technical analysis, research collection, or document production to specialists when that improves speed or quality.",
        ),
    },
}


def repair_memory_home(home: Path, *, agent_name: str) -> RepairResult:
    """Repair one agent/main memory pair using DB and Markdown as mutual fallbacks."""
    db = RuntimeStateDB(home / "state.db")
    repo = MemoryFragmentRepository(db)
    source_path = home / "workspace" / "memory_system" / "MAINMEMORY.md"
    shared_path = _resolve_sharedmemory_path(home)
    existing = repo.list_by_scope("mainmemory", agent_name=agent_name)
    markdown = source_path.read_text(encoding="utf-8") if source_path.exists() else ""

    if existing and _looks_like_empty_template(markdown):
        reverse_sync_source(source_path, existing)
        return RepairResult(agent_name, "db_to_markdown", len(existing), source_path)

    if not existing and markdown.strip() and not _looks_like_empty_template(markdown):
        fragments = extract_markdown_fragments(
            markdown,
            source_path=str(source_path),
            source_kind="mainmemory",
            scope="mainmemory",
            agent_name=agent_name,
        )
        repo.replace_for_scope("mainmemory", fragments, agent_name=agent_name)
        return RepairResult(agent_name, "markdown_to_db", len(fragments), source_path)

    if existing and markdown.strip():
        return RepairResult(agent_name, "ok", len(existing), source_path)

    if _looks_like_empty_template(markdown):
        _bootstrap_role_memory(source_path, agent_name=agent_name)
        synced = _sync_agent_io(shared_path, source_path)
        if synced or source_path.exists():
            fragments = extract_markdown_fragments(
                source_path.read_text(encoding="utf-8"),
                source_path=str(source_path),
                source_kind="mainmemory",
                scope="mainmemory",
                agent_name=agent_name,
            )
            repo.replace_for_scope("mainmemory", fragments, agent_name=agent_name)
            return RepairResult(agent_name, "bootstrap_from_shared", len(fragments), source_path)

    return RepairResult(agent_name, "empty", 0, source_path)


def repair_all(ductor_home: Path) -> list[RepairResult]:
    """Repair main + every sub-agent memory store."""
    results = [repair_memory_home(ductor_home, agent_name="main")]
    agents_dir = ductor_home / "agents"
    if agents_dir.exists():
        results.extend(
            repair_memory_home(agent_dir, agent_name=agent_dir.name)
            for agent_dir in sorted(p for p in agents_dir.iterdir() if p.is_dir())
        )
    return results


def _looks_like_empty_template(text: str) -> bool:
    """Heuristic: detect the stock empty MAINMEMORY template."""
    stripped = text.strip()
    if not stripped:
        return True
    markers = (
        "(Empty -- will be populated as you learn about your human.)",
        "(Empty -- will be populated as the agent learns.)",
        "(Empty -- record important decisions and their reasoning here.)",
    )
    return all(marker in stripped for marker in markers)


def _resolve_sharedmemory_path(home: Path) -> Path:
    """Resolve the global shared-memory path for main or sub-agent homes."""
    if home.parent.name == "agents":
        return home.parent.parent / "SHAREDMEMORY.md"
    return home / "SHAREDMEMORY.md"


def _bootstrap_role_memory(source_path: Path, *, agent_name: str) -> None:
    """Seed a minimal role-aware MAINMEMORY scaffold for empty agents."""
    role = _configured_role_bootstrap(source_path, agent_name=agent_name) or _ROLE_BOOTSTRAP_BY_AGENT.get(
        agent_name
    )
    if role is None:
        role = _generic_role_bootstrap(agent_name)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "# Main Memory\n\n"
        "## About the User\n"
        f"{_render_lines(role['about'])}\n\n"
        "## Learned Facts\n"
        f"{_render_lines(role['facts'])}\n\n"
        "## Decisions and Preferences\n"
        f"{_render_lines(role['decisions'])}\n",
        encoding="utf-8",
    )


def _generic_role_bootstrap(agent_name: str) -> dict[str, tuple[str, ...]]:
    """Fallback role scaffold when no exact agent mapping exists."""
    label = agent_name.replace("-", " ")
    return {
        "about": (
            f"- Role: specialized agent `{label}`.",
            "- Default stance: preserve only durable, high-signal context that improves future turns.",
        ),
        "facts": (
            "- Track recurring user goals, domain context, and reusable operating constraints.",
        ),
        "decisions": (
            "- Prefer compact, stable memory over speculative or one-off observations.",
        ),
    }


def _configured_role_bootstrap(
    source_path: Path,
    *,
    agent_name: str,
) -> dict[str, tuple[str, ...]] | None:
    """Build a role scaffold from persisted config when available."""
    home = source_path.parents[2]
    config_path = home / "config" / "config.json"
    if not config_path.is_file():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    role = str(raw.get("role", "")).strip()
    description = str(raw.get("role_description", "")).strip()
    if not role and not description:
        return None
    about_lines = []
    if role:
        about_lines.append(f"- Role: {role}.")
    if description:
        about_lines.append(f"- Mandate: {description}")
    return {
        "about": tuple(about_lines) or (f"- Role: specialized agent `{agent_name}`.",),
        "facts": (
            "- Preserve durable user context and operating constraints that improve future work in this role.",
        ),
        "decisions": (
            "- Prefer role-specific, reusable memory over transient turn-by-turn details.",
            "- Use subagents or routing for work that is multi-step, cross-functional, or better handled by a specialist.",
        ),
    }


def _render_lines(lines: tuple[str, ...]) -> str:
    """Render bullet lines for seeded memory sections."""
    return "\n".join(lines)
