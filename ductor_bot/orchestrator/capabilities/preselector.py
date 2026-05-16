"""Lightweight capability preselection for provider execution."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ductor_bot.orchestrator.capabilities.models import (
    CapabilityExecutionPlan,
    SelectedSkill,
)
from ductor_bot.workspace.path_aliases import PathAliasEntry, PathAliasRegistry
from ductor_bot.workspace.paths import DuctorPaths
from ductor_bot.workspace.project_state import ProjectStateContext, ensure_project_state

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,}")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "the",
        "this",
        "to",
        "use",
        "with",
        "you",
    }
)
_FILE_INTENT_TERMS = frozenset(
    {
        "repo",
        "repository",
        "file",
        "files",
        "folder",
        "folders",
        "code",
        "project",
        "workspace",
        "debug",
        "fix",
        "test",
        "tests",
        "implement",
        "refactor",
        "module",
        "function",
        "class",
        "error",
        "bug",
        "log",
        "logs",
        "daily",
        "note",
        "notes",
        "obsidian",
        "vault",
        "markdown",
        "config",
    }
)
_FILE_INTENT_PHRASES = (
    "daily note",
    "daily notes",
    "obsidian",
    "vault",
    "markdown",
    "日记",
    "每日笔记",
    "日常笔记",
    "笔记",
    "知识库",
)
_PATHLIKE_RE = re.compile(r"(/mnt/[^\s]+|[A-Za-z]:\\[^\s]+|@[a-zA-Z][a-zA-Z0-9_-]{0,63})")
_COMPLEXITY_TERMS = (
    "design",
    "plan",
    "architecture",
    "implement",
    "build",
    "refactor",
    "system",
    "workflow",
    "router",
    "repair",
    "audit",
    "评估",
    "复盘",
    "规划",
    "计划",
    "设计",
    "架构",
    "实现",
    "编排",
    "流程",
    "修复",
    "排查",
    "验收",
)
_DESIGN_TERMS = ("design", "plan", "architecture", "spec", "规划", "计划", "设计", "方案", "架构")
_EVAL_TERMS = (
    "evaluate",
    "evaluation",
    "review",
    "audit",
    "qa",
    "验收",
    "评估",
    "复盘",
    "失败",
    "错误",
)
_WRITE_TERMS = (
    "write",
    "edit",
    "create",
    "implement",
    "fix",
    "append",
    "save",
    "record",
    "修改",
    "创建",
    "实现",
    "修复",
    "写入",
    "更新",
    "补充",
    "记录",
    "添加",
    "保存",
    "入库",
    "沉淀",
)
_FEEDBACK_LOOKBACK_SECONDS = 30 * 24 * 60 * 60
_FEEDBACK_LIMIT = 200
_FEEDBACK_MIN_SAMPLE = 4
_FEEDBACK_CLAMP = 8
_NEGATIVE_OUTCOMES = frozenset({"timeout", "empty_result", "error", "failed", "failure"})
_POSITIVE_OUTCOMES = frozenset({"success", "recovered"})
_INACTIVE_SKILL_STATUSES = frozenset({"candidate", "suppressed"})


class _OutcomeEventReader(Protocol):
    def list_recent(
        self,
        *,
        provider: str = "",
        flow: str = "",
        since: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Return recent outcome events."""


@dataclass(slots=True)
class _SkillMetadata:
    name: str
    description: str
    path: Path
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class _SkillFeedback:
    samples: int
    modifier: int
    weak: bool


class CapabilityPreselector:
    """Select a minimal per-turn capability set."""

    def __init__(self, paths: DuctorPaths) -> None:
        self._paths = paths
        self._path_aliases = PathAliasRegistry(paths)
        self._cached_skills: tuple[_SkillMetadata, ...] = ()
        self._cached_sig: tuple[tuple[str, int], ...] = ()
        self._outcome_event_repo: _OutcomeEventReader | None = None

    def set_outcome_event_repo(self, repo: _OutcomeEventReader | None) -> None:
        """Attach an optional sync outcome-event reader for routing feedback."""
        self._outcome_event_repo = repo

    def build_plan(self, *, provider: str, message: str) -> CapabilityExecutionPlan:  # noqa: PLR0915
        """Build a provider-agnostic execution plan for one turn."""
        message_tokens = self._tokenize(message)
        alias_entries = self._path_aliases.mentions(message)
        project_entry = self._select_project_entry(alias_entries)
        has_alias_context = bool(alias_entries)
        has_pathlike = bool(_PATHLIKE_RE.search(message))
        has_file_intent = bool(message_tokens & _FILE_INTENT_TERMS) or self._contains_any(
            message,
            _FILE_INTENT_PHRASES,
        )
        include_directories = has_file_intent or has_alias_context or has_pathlike
        runtime_profile = "workspace_read" if include_directories else "chat_light"
        rationale: list[str] = []
        phase = "intake"
        recommended_role = "orchestrator"
        state_files: tuple[str, ...] = ()
        needs_workspace_write = self._contains_any(message, _WRITE_TERMS)
        project_alias: str | None = None
        project_path: str | None = None
        project_purpose: str | None = None
        project_north_star: str | None = None
        project_owner_bot: str | None = None
        if include_directories:
            rationale.append("workspace-context")
            if has_alias_context:
                rationale.append("path-alias")
            elif has_pathlike:
                rationale.append("pathlike")
        else:
            rationale.append("chat-light")

        if project_entry is not None:
            project_alias = project_entry.alias
            project_path = project_entry.host_path or project_entry.path
            project_state = self._ensure_project_state(project_entry)
            state_files = project_state.state_files if project_state is not None else ()
            project_purpose = (
                project_state.purpose if project_state is not None else project_entry.purpose
            )
            project_north_star = project_state.north_star if project_state is not None else None
            project_owner_bot = project_state.owner_bot if project_state is not None else None
            is_complex = self._contains_any(message, _COMPLEXITY_TERMS)
            design_missing = self._missing_state_file(state_files, "DESIGN.md")
            if self._contains_any(message, _EVAL_TERMS):
                phase = "evaluation"
                recommended_role = "auditor"
                rationale.append("evaluation-gate")
            elif self._contains_any(message, _DESIGN_TERMS) or (is_complex and design_missing):
                phase = "design"
                recommended_role = "architect"
                rationale.append("design-gate")
            elif is_complex:
                phase = "execution"
                recommended_role = "engineer"
                rationale.append("execution-gate")
            else:
                phase = "intake"
                recommended_role = "researcher"
                rationale.append("project-intake")

        selected_skills: tuple[SelectedSkill, ...] = ()
        if provider == "gemini":
            selected_skills, feedback_rationale = self._select_skills(message, message_tokens)
            rationale.extend(feedback_rationale)
            if selected_skills:
                rationale.append(f"skills:{','.join(skill.name for skill in selected_skills)}")

        return CapabilityExecutionPlan(
            provider=provider,
            phase=phase,
            recommended_role=recommended_role,
            selected_skills=selected_skills,
            include_directories=include_directories,
            directory_scope=(str(self._paths.workspace),) if include_directories else (),
            runtime_profile=runtime_profile,
            needs_workspace_write=needs_workspace_write,
            state_files=state_files,
            project_alias=project_alias,
            project_path=project_path,
            project_purpose=project_purpose,
            project_north_star=project_north_star,
            project_owner_bot=project_owner_bot,
            rationale=tuple(rationale),
        )

    def _select_skills(
        self,
        message: str,
        message_tokens: frozenset[str],
    ) -> tuple[tuple[SelectedSkill, ...], tuple[str, ...]]:
        skills = self._load_skills()
        lowered = message.lower()
        feedback = self._skill_feedback()
        feedback_rationale: list[str] = []
        scored: list[tuple[int, _SkillMetadata, str]] = []
        for skill in skills:
            candidate = self._score_skill_candidate(
                skill,
                lowered=lowered,
                message_tokens=message_tokens,
                feedback=feedback,
                feedback_rationale=feedback_rationale,
            )
            if candidate is not None:
                scored.append(candidate)

        scored.sort(key=lambda item: (-item[0], item[1].name))
        selected: list[SelectedSkill] = []
        seen: set[str] = set()
        for _score, skill, activation in scored[:3]:
            if skill.name in seen:
                continue
            seen.add(skill.name)
            selected.append(
                SelectedSkill(
                    name=skill.name,
                    source_path=str(skill.path),
                    activation_kind=activation,
                )
            )
        return tuple(selected), tuple(dict.fromkeys(feedback_rationale))

    def _score_skill_candidate(
        self,
        skill: _SkillMetadata,
        *,
        lowered: str,
        message_tokens: frozenset[str],
        feedback: Mapping[str, _SkillFeedback],
        feedback_rationale: list[str],
    ) -> tuple[int, _SkillMetadata, str] | None:
        name_hit = skill.name.lower() in lowered
        score = 0
        activation = "heuristic"
        if name_hit:
            score += 100
            activation = "direct"
        overlap = len(message_tokens & skill.tokens)
        if overlap:
            score += overlap * 5
        if not score or (overlap < 2 and not name_hit):
            return None
        skill_feedback = feedback.get(skill.name)
        if activation == "heuristic" and skill_feedback is not None:
            score += self._apply_feedback_modifier(skill.name, skill_feedback, feedback_rationale)
        return score, skill, activation

    @staticmethod
    def _apply_feedback_modifier(
        skill_name: str,
        skill_feedback: _SkillFeedback,
        feedback_rationale: list[str],
    ) -> int:
        if skill_feedback.weak:
            feedback_rationale.append(f"feedback:skill={skill_name}:weak-sample")
            return 0
        if not skill_feedback.modifier:
            return 0
        feedback_rationale.append(f"feedback:skill={skill_name}:{skill_feedback.modifier:+d}")
        return skill_feedback.modifier

    def _skill_feedback(self) -> dict[str, _SkillFeedback]:
        events = self._recent_feedback_events()
        samples: dict[str, list[int]] = {}
        for event in events:
            event_score = self._event_feedback_score(event)
            for name in self._heuristic_skill_names_from_event(event):
                samples.setdefault(name, []).append(event_score)

        feedback: dict[str, _SkillFeedback] = {}
        for name, scores in samples.items():
            if len(scores) < _FEEDBACK_MIN_SAMPLE:
                feedback[name] = _SkillFeedback(samples=len(scores), modifier=0, weak=True)
                continue
            modifier = max(-_FEEDBACK_CLAMP, min(_FEEDBACK_CLAMP, sum(scores) * 2))
            feedback[name] = _SkillFeedback(
                samples=len(scores),
                modifier=modifier,
                weak=False,
            )
        return feedback

    def _recent_feedback_events(self) -> list[dict[str, object]]:
        repo = self._outcome_event_repo
        if repo is None:
            return []
        try:
            return repo.list_recent(
                provider="gemini",
                since=time.time() - _FEEDBACK_LOOKBACK_SECONDS,
                limit=_FEEDBACK_LIMIT,
            )
        except Exception:
            logger.exception("Failed to load capability outcome feedback")
            return []

    @staticmethod
    def _heuristic_skill_names_from_event(event: Mapping[str, Any]) -> tuple[str, ...]:
        payload = event.get("payload_json")
        if not isinstance(payload, Mapping):
            return ()
        plan = payload.get("capability_plan")
        if not isinstance(plan, Mapping):
            return ()
        selected = plan.get("selected_skills")
        if not isinstance(selected, Sequence) or isinstance(selected, str | bytes):
            return ()
        names: list[str] = []
        for raw_skill in selected:
            if not isinstance(raw_skill, Mapping):
                continue
            if raw_skill.get("activation_kind") != "heuristic":
                continue
            name = raw_skill.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return tuple(names)

    @staticmethod
    def _event_feedback_score(event: Mapping[str, Any]) -> int:
        outcome = str(event.get("outcome") or "").lower()
        failure_class = str(event.get("failure_class") or "").lower()
        if bool(event.get("empty_result")) or outcome in _NEGATIVE_OUTCOMES:
            return -1
        if failure_class in {"timeout", "empty_result"}:
            return -1
        if outcome in _POSITIVE_OUTCOMES:
            return 1
        return 0

    def _load_skills(self) -> tuple[_SkillMetadata, ...]:
        skills_dir = self._paths.skills_dir
        if not skills_dir.is_dir():
            return ()
        current_sig: list[tuple[str, int]] = []
        candidates: list[Path] = []
        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            skill_md = entry / "SKILL.md"
            if not entry.is_dir() or not skill_md.is_file():
                continue
            try:
                mtime_ns = skill_md.stat().st_mtime_ns
            except OSError:
                continue
            current_sig.append((entry.name, mtime_ns))
            candidates.append(entry)

        sig = tuple(current_sig)
        if sig == self._cached_sig:
            return self._cached_skills

        parsed: list[_SkillMetadata] = []
        for entry in candidates:
            metadata = self._parse_skill(entry)
            if metadata is not None:
                parsed.append(metadata)

        self._cached_sig = sig
        self._cached_skills = tuple(parsed)
        logger.debug("CapabilityPreselector indexed %d skills", len(parsed))
        return self._cached_skills

    @staticmethod
    def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(needle in lowered or needle in text for needle in needles)

    @staticmethod
    def _select_project_entry(entries: Sequence[PathAliasEntry]) -> PathAliasEntry | None:
        for entry in entries:
            normalized = entry.path.replace("\\", "/")
            if entry.alias.startswith("p-") or "/100_Project/" in normalized:
                return entry
        return entries[0] if entries else None

    @staticmethod
    def _project_state_files(entry: PathAliasEntry) -> tuple[str, ...]:
        root = entry.host_path or entry.path
        root_path = Path(root)
        if not root_path.is_absolute():
            return ()
        return (
            str(root_path / "DESIGN.md"),
            str(root_path / "TASKS.md"),
            str(root_path / "EVALUATE.md"),
        )

    @staticmethod
    def _ensure_project_state(entry: PathAliasEntry) -> ProjectStateContext | None:
        root = entry.host_path or entry.path
        root_path = Path(root)
        if not root_path.is_absolute():
            return None
        return ensure_project_state(
            root_path=root_path,
            alias=entry.alias,
            purpose=entry.purpose,
        )

    @staticmethod
    def _missing_state_file(state_files: tuple[str, ...], name: str) -> bool:
        for item in state_files:
            if item.endswith(name):
                return not Path(item).exists()
        return True

    def _parse_skill(self, skill_dir: Path) -> _SkillMetadata | None:
        skill_md = skill_dir / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            return None

        frontmatter_match = _FRONTMATTER_RE.match(content)
        if frontmatter_match:
            frontmatter = self._parse_frontmatter(frontmatter_match.group(1))
        else:
            frontmatter = {}

        name = str(frontmatter.get("name") or skill_dir.name).strip()
        status = str(frontmatter.get("status") or "").strip().lower()
        if status in _INACTIVE_SKILL_STATUSES:
            return None
        description = str(frontmatter.get("description") or "").strip()
        tokens = self._tokenize(f"{name} {description}")
        if not tokens:
            tokens = frozenset(self._tokenize(name))
        return _SkillMetadata(name=name, description=description, path=skill_dir, tokens=tokens)

    @staticmethod
    def _parse_frontmatter(frontmatter: str) -> dict[str, str]:
        data: dict[str, str] = {}
        for raw_line in frontmatter.splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip("'\"")
        return data

    @staticmethod
    def _tokenize(text: str) -> frozenset[str]:
        tokens = {
            token
            for token in _TOKEN_RE.findall(text.lower())
            if token not in _STOPWORDS and len(token) > 1
        }
        return frozenset(tokens)
