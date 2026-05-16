from __future__ import annotations

import time
from pathlib import Path

from ductor_bot.orchestrator.capabilities.preselector import CapabilityPreselector
from ductor_bot.workspace.path_aliases import AliasRegistration, PathAliasRegistry
from ductor_bot.workspace.paths import DuctorPaths


def _make_paths(tmp_path: Path) -> DuctorPaths:
    return DuctorPaths(
        ductor_home=tmp_path / "ductor_home",
        home_defaults=tmp_path / "fw" / "_home_defaults",
        framework_root=tmp_path / "fw",
    )


def _make_skill(
    base: Path,
    name: str,
    description: str,
    *,
    status: str | None = None,
) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    status_line = "" if status is None else f"status: {status}\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\n{status_line}name: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


class _FakeOutcomeRepo:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events

    def list_recent(
        self,
        *,
        provider: str = "",
        flow: str = "",
        since: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        rows = [
            event
            for event in self.events
            if (not provider or event.get("provider") == provider)
            and (not flow or event.get("flow") == flow)
            and (since is None or float(event.get("created_at", 0)) >= since)
        ]
        return rows[:limit]


def _skill_event(
    skill_name: str,
    *,
    outcome: str = "timeout",
    created_at: float | None = None,
    activation_kind: str = "heuristic",
) -> dict[str, object]:
    return {
        "provider": "gemini",
        "flow": "normal",
        "outcome": outcome,
        "failure_class": outcome if outcome in {"timeout", "empty_result"} else "",
        "empty_result": outcome == "empty_result",
        "created_at": created_at if created_at is not None else time.time(),
        "payload_json": {
            "capability_plan": {
                "selected_skills": [
                    {"name": skill_name, "activation_kind": activation_kind},
                ],
            },
        },
    }


def test_plain_chat_is_lightweight(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(paths.skills_dir, "browser-testing", "Use when debugging browser issues")
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="你好")

    assert plan.runtime_profile == "chat_light"
    assert plan.include_directories is False
    assert plan.selected_skills == ()


def test_explicit_skill_name_is_selected(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    skill_dir = _make_skill(
        paths.skills_dir,
        "brainstorming",
        "Use before creative feature work and design exploration",
    )
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="use brainstorming for this feature")

    assert len(plan.selected_skills) == 1
    assert plan.selected_skills[0].name == "brainstorming"
    assert plan.selected_skills[0].source_path == str(skill_dir)


def test_candidate_and_suppressed_skills_are_not_selected(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(
        paths.skills_dir,
        "candidate-skill",
        "debug browser test support",
        status="candidate",
    )
    _make_skill(
        paths.skills_dir,
        "suppressed-skill",
        "debug browser test support",
        status="suppressed",
    )
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="debug browser test candidate-skill")

    assert plan.selected_skills == ()


def test_suppressed_direct_skill_name_is_not_selected(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(
        paths.skills_dir,
        "suppressed-skill",
        "Use when debugging browser issues",
        status="suppressed",
    )
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="use suppressed-skill")

    assert plan.selected_skills == ()


def test_skill_without_status_stays_selectable(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(paths.skills_dir, "legacy-skill", "Use when debugging browser issues")
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="use legacy-skill")

    assert len(plan.selected_skills) == 1
    assert plan.selected_skills[0].name == "legacy-skill"


def test_feedback_low_sample_is_weak_and_does_not_change_order(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(paths.skills_dir, "alpha-skill", "debug browser test support")
    _make_skill(paths.skills_dir, "beta-skill", "debug browser test support")
    selector = CapabilityPreselector(paths)
    selector.set_outcome_event_repo(
        _FakeOutcomeRepo([_skill_event("alpha-skill"), _skill_event("alpha-skill")])
    )

    plan = selector.build_plan(provider="gemini", message="debug browser test")

    assert [skill.name for skill in plan.selected_skills[:2]] == ["alpha-skill", "beta-skill"]
    assert "feedback:skill=alpha-skill:weak-sample" in plan.rationale


def test_feedback_stale_events_do_not_affect_order(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(paths.skills_dir, "alpha-skill", "debug browser test support")
    _make_skill(paths.skills_dir, "beta-skill", "debug browser test support")
    old = time.time() - (31 * 24 * 60 * 60)
    selector = CapabilityPreselector(paths)
    selector.set_outcome_event_repo(
        _FakeOutcomeRepo([_skill_event("alpha-skill", created_at=old) for _ in range(6)])
    )

    plan = selector.build_plan(provider="gemini", message="debug browser test")

    assert [skill.name for skill in plan.selected_skills[:2]] == ["alpha-skill", "beta-skill"]
    assert not any(item.startswith("feedback:skill=alpha-skill") for item in plan.rationale)


def test_feedback_bounded_demotion_can_drop_heuristic_skill(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(paths.skills_dir, "alpha-skill", "debug browser test support")
    _make_skill(paths.skills_dir, "beta-skill", "debug browser test support")
    _make_skill(paths.skills_dir, "gamma-skill", "debug browser test support")
    _make_skill(paths.skills_dir, "zeta-skill", "debug browser test support")
    selector = CapabilityPreselector(paths)
    selector.set_outcome_event_repo(
        _FakeOutcomeRepo([_skill_event("alpha-skill", outcome="empty_result") for _ in range(8)])
    )

    plan = selector.build_plan(provider="gemini", message="debug browser test")

    assert [skill.name for skill in plan.selected_skills] == [
        "beta-skill",
        "gamma-skill",
        "zeta-skill",
    ]
    assert "feedback:skill=alpha-skill:-8" in plan.rationale


def test_feedback_does_not_demote_direct_skill_name(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    _make_skill(paths.skills_dir, "alpha-skill", "debug browser test support")
    selector = CapabilityPreselector(paths)
    selector.set_outcome_event_repo(
        _FakeOutcomeRepo([_skill_event("alpha-skill", outcome="timeout") for _ in range(8)])
    )

    plan = selector.build_plan(provider="gemini", message="use alpha-skill for debug browser test")

    assert len(plan.selected_skills) == 1
    assert plan.selected_skills[0].name == "alpha-skill"
    assert plan.selected_skills[0].activation_kind == "direct"


def test_workspace_request_enables_directory_context(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="帮我看看这个 repo 里的报错日志")

    assert plan.include_directories is True
    assert plan.runtime_profile == "workspace_read"


def test_daily_note_update_needs_workspace_context_and_write(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    selector = CapabilityPreselector(paths)

    plan = selector.build_plan(provider="gemini", message="请把这条记录补充到 Daily Notes")

    assert plan.include_directories is True
    assert plan.runtime_profile == "workspace_read"
    assert plan.needs_workspace_write is True


def test_project_alias_complex_task_gates_to_design(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.skills_dir.mkdir(parents=True)
    paths.config_dir.mkdir(parents=True)
    project_root = tmp_path / "vault" / "100_Project" / "2604_HK"
    project_root.mkdir(parents=True)
    paths.config_path.write_text(
        '{"docker":{"mounts":["'
        + str((tmp_path / "vault").resolve()).replace("\\", "\\\\")
        + '"]}}',
        encoding="utf-8",
    )
    registry = PathAliasRegistry(paths)
    registry.upsert(
        AliasRegistration(
            alias="p-hk",
            path=str(project_root),
            purpose="香港就业机会与优才计划",
        )
    )
    selector = CapabilityPreselector(paths)
    _make_skill(paths.skills_dir, "design-routing", "design plan architecture support")
    selector.set_outcome_event_repo(
        _FakeOutcomeRepo([_skill_event("design-routing", outcome="timeout") for _ in range(8)])
    )

    plan = selector.build_plan(provider="gemini", message="@p-hk 请先设计整体方案, 再规划执行路径")

    assert plan.phase == "design"
    assert plan.recommended_role == "architect"
    assert plan.project_alias == "p-hk"
    assert len(plan.state_files) == 3
    assert plan.include_directories is True
