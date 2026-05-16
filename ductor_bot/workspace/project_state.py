"""Project-state protocol backed by DESIGN.md frontmatter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_PROTOCOL_HEADER = (
    "> Capability Router Protocol\n"
    "> This file is a long-lived project state file.\n"
    "> Do not rewrite this file wholesale.\n"
    "> Only append new entries or edit explicitly conflicting fields after user confirmation.\n"
    "> If a request conflicts with existing content, surface the conflict first.\n"
)


@dataclass(frozen=True, slots=True)
class ProjectStateContext:
    """Normalized protocol state for one project root."""

    alias: str
    root_path: str
    purpose: str
    north_star: str
    owner_bot: str
    domain: str
    quality_bar: tuple[str, ...]
    requires_design: bool
    requires_plan: bool
    requires_evaluation: bool
    design_path: str
    tasks_path: str
    evaluate_path: str

    @property
    def state_files(self) -> tuple[str, str, str]:
        return (self.design_path, self.tasks_path, self.evaluate_path)


def ensure_project_state(
    *,
    root_path: str | Path,
    alias: str,
    purpose: str = "",
    owner_bot: str = "",
) -> ProjectStateContext | None:
    """Create or upgrade project protocol files around one root path."""
    root = Path(root_path)
    if not root.exists() or not root.is_dir():
        return None
    design_path = root / "DESIGN.md"
    tasks_path = root / "TASKS.md"
    evaluate_path = root / "EVALUATE.md"

    design_text = design_path.read_text(encoding="utf-8") if design_path.exists() else "# DESIGN\n"
    metadata, body = _extract_frontmatter(design_text)
    defaults = _default_metadata(alias=alias, purpose=purpose, owner_bot=owner_bot)
    merged = _merge_metadata(metadata, defaults)
    _write_design(design_path, merged, body)
    _ensure_tasks(tasks_path, merged)
    _ensure_evaluate(evaluate_path, merged)
    return load_project_state(root_path=root, alias_hint=alias, purpose_hint=purpose)


def load_project_state(
    *,
    root_path: str | Path,
    alias_hint: str = "",
    purpose_hint: str = "",
) -> ProjectStateContext | None:
    """Load one project protocol context from DESIGN.md frontmatter."""
    root = Path(root_path)
    design_path = root / "DESIGN.md"
    if not design_path.exists():
        return None
    metadata, _body = _extract_frontmatter(design_path.read_text(encoding="utf-8"))
    alias = str(metadata.get("alias") or alias_hint).strip().lstrip("@")
    if not alias:
        return None
    purpose = str(metadata.get("purpose") or purpose_hint).strip()
    north_star = str(metadata.get("north_star") or "").strip()
    owner_bot = str(metadata.get("owner_bot") or "").strip()
    domain = str(metadata.get("domain") or "").strip()
    quality_bar = tuple(_coerce_list(metadata.get("quality_bar")))
    return ProjectStateContext(
        alias=alias,
        root_path=str(root),
        purpose=purpose,
        north_star=north_star,
        owner_bot=owner_bot,
        domain=domain,
        quality_bar=quality_bar,
        requires_design=_coerce_bool(metadata.get("requires_design"), default=True),
        requires_plan=_coerce_bool(metadata.get("requires_plan"), default=True),
        requires_evaluation=_coerce_bool(metadata.get("requires_evaluation"), default=True),
        design_path=str(design_path),
        tasks_path=str(root / "TASKS.md"),
        evaluate_path=str(root / "EVALUATE.md"),
    )


def render_project_state_prompt(
    context: ProjectStateContext,
    *,
    phase: str,
    recommended_role: str,
) -> str:
    """Render one concise protocol prompt for provider execution."""
    lines = [
        "## Project State Protocol",
        f"Active project alias: @{context.alias}",
        f"Project root: `{context.root_path}`",
        f"Current phase: {phase}",
        f"Recommended role: {recommended_role}",
    ]
    if context.purpose:
        lines.append(f"Purpose: {context.purpose}")
    if context.north_star:
        lines.append(f"North Star: {context.north_star}")
    if context.owner_bot:
        lines.append(f"Owner Bot: {context.owner_bot}")
    if context.domain:
        lines.append(f"Domain: {context.domain}")
    if context.quality_bar:
        lines.append("Quality Bar:")
        lines.extend(f"- {item}" for item in context.quality_bar)
    lines.extend(
        (
            "",
            "State files:",
            f"- DESIGN.md: `{context.design_path}`",
            f"- TASKS.md: `{context.tasks_path}`",
            f"- EVALUATE.md: `{context.evaluate_path}`",
            "",
            "Protocol:",
            "- DESIGN.md frontmatter is the project identity card and operating contract.",
            "- TASKS.md tracks doing/next/done/backlog against the north star.",
            "- EVALUATE.md records failure definitions, risks, and verification outcomes.",
        )
    )
    design_excerpt = _read_excerpt(Path(context.design_path))
    tasks_excerpt = _read_excerpt(Path(context.tasks_path))
    evaluate_excerpt = _read_excerpt(Path(context.evaluate_path))
    if design_excerpt:
        lines.extend(("", "### DESIGN Snapshot", design_excerpt))
    if tasks_excerpt:
        lines.extend(("", "### TASKS Snapshot", tasks_excerpt))
    if evaluate_excerpt:
        lines.extend(("", "### EVALUATE Snapshot", evaluate_excerpt))
    return "\n".join(lines)


def _default_metadata(*, alias: str, purpose: str, owner_bot: str) -> dict[str, object]:
    base_purpose = purpose or f"Drive durable progress for @{alias}."
    return {
        "alias": alias,
        "purpose": base_purpose,
        "north_star": f"Deliver durable progress for {base_purpose.rstrip('.')}.",
        "owner_bot": owner_bot,
        "domain": "project",
        "quality_bar": [
            "Outputs stay aligned with the project goal and current design.",
            "Complex work updates DESIGN.md, TASKS.md, and EVALUATE.md coherently.",
            "Failures and verification outcomes are recorded, not hidden.",
        ],
        "requires_design": True,
        "requires_plan": True,
        "requires_evaluation": True,
    }


def _merge_metadata(current: dict[str, object], defaults: dict[str, object]) -> dict[str, object]:
    merged = dict(current)
    for key, value in defaults.items():
        existing = merged.get(key)
        if existing in ("", None, []):
            merged[key] = value
    current_alias = str(merged.get("alias") or "").strip().lstrip("@")
    default_alias = str(defaults.get("alias") or "").strip().lstrip("@")
    if default_alias.startswith("p-") and current_alias and not current_alias.startswith("p-"):
        merged["alias"] = default_alias
    return merged


def _extract_frontmatter(text: str) -> tuple[dict[str, object], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    return _parse_frontmatter(match.group(1)), text[match.end() :]


def _parse_frontmatter(frontmatter: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_list_key: str | None = None
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and current_list_key:
            current_list = data.setdefault(current_list_key, [])
            if isinstance(current_list, list):
                current_list.append(stripped[2:].strip())
            continue
        current_list_key = None
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if value == "":
            data[key] = []
            current_list_key = key
            continue
        data[key] = value
    return data


def _render_frontmatter(metadata: dict[str, object]) -> str:
    lines = ["---"]
    ordered_keys = (
        "alias",
        "purpose",
        "north_star",
        "owner_bot",
        "domain",
        "quality_bar",
        "requires_design",
        "requires_plan",
        "requires_evaluation",
    )
    for key in ordered_keys:
        if key not in metadata:
            continue
        value = metadata[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in cast("list[object]", value))
            continue
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _write_design(path: Path, metadata: dict[str, object], body: str) -> None:
    normalized_body = body.lstrip("\n")
    content = f"{_render_frontmatter(metadata)}\n\n{normalized_body}".rstrip() + "\n"
    path.write_text(content, encoding="utf-8")


def _ensure_tasks(path: Path, metadata: dict[str, object]) -> None:
    if path.exists():
        return
    alias = metadata.get("alias", "")
    north_star = metadata.get("north_star", "")
    path.write_text(
        (
            f"{_PROTOCOL_HEADER}\n"
            "# TASKS\n\n"
            "## Objective\n"
            f"- Alias: @{alias}\n"
            f"- North Star: {north_star}\n\n"
            "## Doing\n"
            "- \n\n"
            "## Next\n"
            "- \n\n"
            "## Done\n"
            "- \n\n"
            "## Backlog\n"
            "- \n"
        ),
        encoding="utf-8",
    )


def _ensure_evaluate(path: Path, metadata: dict[str, object]) -> None:
    if path.exists():
        return
    quality_bar = _coerce_list(metadata.get("quality_bar"))
    lines = [
        _PROTOCOL_HEADER,
        "# EVALUATE\n",
        "## Success Criteria",
    ]
    if quality_bar:
        lines.extend(f"- {item}" for item in quality_bar)
    else:
        lines.append("- ")
    lines.extend(("", "## Failure Modes", "- ", "", "## Review Log", "- "))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _coerce_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "1"}:
        return True
    if lowered in {"false", "no", "0"}:
        return False
    return default


def _coerce_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _read_excerpt(path: Path, *, max_lines: int = 18) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return ""
    return "\n".join(lines[:max_lines])
