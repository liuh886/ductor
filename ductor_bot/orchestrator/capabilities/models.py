"""Models for provider-agnostic capability preselection."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SelectedSkill:
    """One skill selected for the current turn."""

    name: str
    source_path: str
    activation_kind: str = "heuristic"


@dataclass(frozen=True, slots=True)
class CapabilityExecutionPlan:
    """Provider-agnostic execution plan for one turn."""

    provider: str
    phase: str | None = None
    recommended_role: str | None = None
    selected_skills: tuple[SelectedSkill, ...] = ()
    include_directories: bool = False
    directory_scope: tuple[str, ...] = ()
    memory_mode: str = "default"
    runtime_profile: str = "chat_light"
    needs_workspace_write: bool = False
    state_files: tuple[str, ...] = ()
    project_alias: str | None = None
    project_path: str | None = None
    project_purpose: str | None = None
    project_north_star: str | None = None
    project_owner_bot: str | None = None
    rationale: tuple[str, ...] = field(default_factory=tuple)
