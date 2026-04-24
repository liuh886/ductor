"""Heuristics for keeping runtime memory fragments compact and coherent."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ductor_bot.runtime.memory.extractor import MemoryFragment


@dataclass(frozen=True, slots=True)
class MemoryConflict:
    """A likely contradiction between fragments with the same semantic slot."""

    title: str
    scope: str
    agent_name: str
    ulids: tuple[str, ...]
    bodies: tuple[str, ...]


def govern_fragments(fragments: list[MemoryFragment]) -> tuple[list[MemoryFragment], list[MemoryConflict]]:
    """Return a de-duplicated fragment set plus any unresolved conflicts.

    Governance performs three passes:
    - exact duplicate merge
    - lossy-safe compression for same-title subset fragments
    - conflict detection for same-title fragments that still disagree
    """
    deduped = _deduplicate_exact(fragments)
    compressed = _compress_title_groups(deduped)
    conflicts = detect_conflicts(compressed)
    return compressed, conflicts


def detect_conflicts(fragments: list[MemoryFragment]) -> list[MemoryConflict]:
    """Return likely conflicting fragments that share the same semantic title."""
    groups: dict[tuple[str, str, str], list[MemoryFragment]] = {}
    for fragment in fragments:
        key = (fragment.scope, fragment.agent_name, _normalize_title(fragment.title))
        groups.setdefault(key, []).append(fragment)

    conflicts: list[MemoryConflict] = []
    for (scope, agent_name, title_key), group in groups.items():
        unique_bodies: dict[str, MemoryFragment] = {}
        for fragment in group:
            unique_bodies.setdefault(_normalize_body(fragment.body), fragment)
        if len(unique_bodies) <= 1:
            continue
        sorted_group = sorted(unique_bodies.values(), key=lambda item: item.importance, reverse=True)
        conflicts.append(
            MemoryConflict(
                title=sorted_group[0].title if sorted_group else title_key,
                scope=scope,
                agent_name=agent_name,
                ulids=tuple(fragment.ulid for fragment in sorted_group),
                bodies=tuple(fragment.body for fragment in sorted_group),
            )
        )
    return conflicts


def _deduplicate_exact(fragments: list[MemoryFragment]) -> list[MemoryFragment]:
    """Merge exact semantic duplicates and keep the richest surviving row."""
    merged: dict[tuple[str, str, str, str], MemoryFragment] = {}
    counts: dict[tuple[str, str, str, str], int] = {}

    for fragment in fragments:
        key = (
            fragment.scope,
            fragment.agent_name,
            _normalize_title(fragment.title),
            _normalize_body(fragment.body),
        )
        counts[key] = counts.get(key, 0) + 1
        existing = merged.get(key)
        if existing is None:
            merged[key] = fragment
            continue
        merged[key] = _merge_duplicate_pair(existing, fragment, duplicate_count=counts[key])

    return list(merged.values())


def _compress_title_groups(fragments: list[MemoryFragment]) -> list[MemoryFragment]:
    """Collapse subset fragments that share the same title into a richer parent."""
    groups: dict[tuple[str, str, str], list[MemoryFragment]] = {}
    for fragment in fragments:
        key = (fragment.scope, fragment.agent_name, _normalize_title(fragment.title))
        groups.setdefault(key, []).append(fragment)

    result: list[MemoryFragment] = []
    for group in groups.values():
        pending = sorted(group, key=lambda item: len(_body_lines(item.body)), reverse=True)
        kept: list[MemoryFragment] = []
        for fragment in pending:
            matched = False
            for index, current in enumerate(kept):
                if _is_subset_fragment(fragment, current):
                    kept[index] = _merge_subset_pair(current, fragment)
                    matched = True
                    break
                if _is_subset_fragment(current, fragment):
                    kept[index] = _merge_subset_pair(fragment, current)
                    matched = True
                    break
            if not matched:
                kept.append(fragment)
        result.extend(kept)
    return result


def _merge_duplicate_pair(
    left: MemoryFragment,
    right: MemoryFragment,
    *,
    duplicate_count: int,
) -> MemoryFragment:
    """Merge two exact duplicates while preserving stable identifiers."""
    merged_tags = list(dict.fromkeys([*left.tags, *right.tags]))
    return replace(
        left,
        tags=merged_tags,
        importance=round(max(left.importance, right.importance) + min(duplicate_count - 1, 5) * 0.05, 3),
        created_at=_pick_positive_min(left.created_at, right.created_at),
        updated_at=max(left.updated_at, right.updated_at),
    )


def _merge_subset_pair(primary: MemoryFragment, secondary: MemoryFragment) -> MemoryFragment:
    """Merge a subset fragment into the primary fragment without losing tags or recency."""
    merged_tags = list(dict.fromkeys([*primary.tags, *secondary.tags]))
    return replace(
        primary,
        tags=merged_tags,
        importance=round(max(primary.importance, secondary.importance) + 0.1, 3),
        created_at=_pick_positive_min(primary.created_at, secondary.created_at),
        updated_at=max(primary.updated_at, secondary.updated_at),
    )


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def _normalize_body(body: str) -> str:
    return "\n".join(_body_lines(body))


def _body_lines(body: str) -> tuple[str, ...]:
    return tuple(line.strip().lower() for line in body.splitlines() if line.strip())


def _is_subset_fragment(candidate: MemoryFragment, container: MemoryFragment) -> bool:
    """True when candidate adds no information beyond the container fragment."""
    candidate_lines = set(_body_lines(candidate.body))
    container_lines = set(_body_lines(container.body))
    return bool(candidate_lines) and candidate_lines.issubset(container_lines)


def _pick_positive_min(left: float, right: float) -> float:
    values = [value for value in (left, right) if value > 0]
    return min(values) if values else 0.0
