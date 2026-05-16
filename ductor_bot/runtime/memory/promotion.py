"""Verification and orchestration for promoting journaled memory candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ductor_bot.runtime.memory.extractor import MemoryFragment, extract_markdown_fragments
from ductor_bot.runtime.memory.governance import detect_conflicts, govern_fragments

if TYPE_CHECKING:
    from ductor_bot.runtime.state.repositories.memory_fragment_repo import MemoryFragmentRepository
    from ductor_bot.runtime.state.repositories.memory_promotion_journal_repo import (
        MemoryPromotionJournalRepository,
    )
    from ductor_bot.runtime.state.repositories.message_repo import MessageRepository

_ALLOWED_SCOPES = {"mainmemory", "sharedmemory"}


@dataclass(frozen=True, slots=True)
class MemoryPromotionResult:
    """Outcome of candidate verification or promotion."""

    accepted: bool
    status: str
    metadata: dict[str, object]
    promoted_fragment_ulid: str = ""


def verify_memory_candidate(
    candidate: dict[str, object] | None,
    message_repo: MessageRepository,
    fragment_repo: MemoryFragmentRepository,
) -> MemoryPromotionResult:
    """Verify that a pending journal candidate may be promoted."""
    shape_result = _validate_candidate_shape(candidate)
    if shape_result is not None:
        return shape_result

    if candidate is None:
        return _rejected("missing_candidate")

    source_result = _validate_source_messages(candidate, message_repo)
    if source_result is not None:
        return source_result

    shared_result = _validate_shared_candidate(candidate)
    if shared_result is not None:
        return shared_result

    return _validate_candidate_governance(candidate, fragment_repo)


def promote_memory_candidate(  # noqa: PLR0913
    candidate_id: int,
    journal_repo: MemoryPromotionJournalRepository,
    message_repo: MessageRepository,
    fragment_repo: MemoryFragmentRepository,
    *,
    mainmemory_path: Path,
    sharedmemory_path: Path,
) -> MemoryPromotionResult:
    """Verify, append Markdown, re-extract fragments, and update the journal."""
    candidate = journal_repo.get(candidate_id)
    result = verify_memory_candidate(candidate, message_repo, fragment_repo)
    if not result.accepted:
        journal_repo.set_status(candidate_id, result.status, verification=result.metadata)
        return result

    if candidate is None:
        journal_repo.set_status(candidate_id, "rejected", verification={"reason": "missing_candidate"})
        return _rejected("missing_candidate")

    target_scope = str(candidate["target_scope"])
    target_path = sharedmemory_path if target_scope == "sharedmemory" else mainmemory_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    original = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    addition = _candidate_markdown(candidate)
    updated = _append_markdown(original, addition)
    if target_scope == "sharedmemory":
        secret_result = _verify_shared_guardrail_text(updated)
        if secret_result is not None:
            journal_repo.set_status(candidate_id, secret_result.status, verification=secret_result.metadata)
            return secret_result

    target_path.write_text(updated, encoding="utf-8")

    agent_name = _agent_name_for_scope(candidate)
    extracted = extract_markdown_fragments(
        updated,
        source_path=str(target_path),
        source_kind=target_scope,
        scope=target_scope,
        agent_name=agent_name,
    )
    fragment_repo.replace_for_scope(target_scope, extracted, agent_name=agent_name)

    promoted_ulid = _find_promoted_ulid(candidate, extracted)
    metadata = {
        "reason": "promoted",
        "fragment_count": len(extracted),
        "target_scope": target_scope,
    }
    journal_repo.mark_promoted(candidate_id, promoted_ulid, verification=metadata)
    return MemoryPromotionResult(
        accepted=True,
        status="promoted",
        metadata=metadata,
        promoted_fragment_ulid=promoted_ulid,
    )


def _validate_candidate_shape(candidate: dict[str, object] | None) -> MemoryPromotionResult | None:
    if candidate is None:
        return _rejected("missing_candidate")

    status = str(candidate.get("status", "")).strip()
    if status != "pending":
        return _needs_review("not_pending", current_status=status)

    title = str(candidate.get("title", "")).strip()
    body = str(candidate.get("body", "")).strip()
    if not title or not body:
        return _rejected("empty_candidate")

    target_scope = str(candidate.get("target_scope", "")).strip()
    if target_scope not in _ALLOWED_SCOPES:
        return _rejected("invalid_target_scope", target_scope=target_scope)
    return None


def _validate_source_messages(
    candidate: dict[str, object],
    message_repo: MessageRepository,
) -> MemoryPromotionResult | None:
    source_message_ids = _source_message_ids(candidate)
    if not source_message_ids:
        return _rejected("missing_source_message")

    session_storage_key = str(candidate.get("session_storage_key", ""))
    has_evidence_text = False
    for message_id in source_message_ids:
        message = message_repo.get(message_id)
        if message is None:
            return _rejected("missing_source_message", message_id=message_id)
        if str(message.get("session_storage_key", "")) != session_storage_key:
            return _rejected("session_mismatch", message_id=message_id)
        content_text = str(message.get("content_text", "")).strip()
        thought = str(message.get("thought", "")).strip()
        has_evidence_text = has_evidence_text or bool(content_text or thought)
    if not has_evidence_text:
        return _rejected("empty_source_evidence")
    return None


def _validate_shared_candidate(candidate: dict[str, object]) -> MemoryPromotionResult | None:
    if str(candidate.get("target_scope", "")).strip() == "sharedmemory":
        secret_result = _verify_shared_guardrail(
            str(candidate.get("title", "")).strip(),
            str(candidate.get("body", "")).strip(),
        )
        if secret_result is not None:
            return secret_result
    return None


def _validate_candidate_governance(
    candidate: dict[str, object],
    fragment_repo: MemoryFragmentRepository,
) -> MemoryPromotionResult:
    target_scope = str(candidate.get("target_scope", "")).strip()
    agent_name = _agent_name_for_scope(candidate)
    existing = _rows_to_fragments(fragment_repo.list_by_scope(target_scope, agent_name=agent_name))
    candidate_fragment = _candidate_fragment(candidate)

    duplicate_reason = _duplicate_or_no_new_info(candidate_fragment, existing)
    if duplicate_reason:
        return _rejected(duplicate_reason)

    governed, governance_conflicts = govern_fragments([*existing, candidate_fragment])
    conflicts = [
        conflict
        for conflict in (*governance_conflicts, *detect_conflicts(governed))
        if _same_title(conflict.title, candidate_fragment.title)
        and conflict.scope == target_scope
        and conflict.agent_name == agent_name
    ]
    if conflicts:
        return _rejected("conflict", conflict_count=len(conflicts))

    return MemoryPromotionResult(
        accepted=True,
        status="promoted",
        metadata={
            "reason": "accepted",
            "source_message_count": len(_source_message_ids(candidate)),
            "target_scope": target_scope,
        },
    )


def _candidate_fragment(candidate: dict[str, object]) -> MemoryFragment:
    target_scope = str(candidate.get("target_scope", "")).strip()
    return MemoryFragment(
        title=str(candidate.get("title", "")).strip(),
        body=str(candidate.get("body", "")).strip(),
        scope=target_scope,
        agent_name=_agent_name_for_scope(candidate),
        tags=[str(tag) for tag in _list_value(candidate.get("tags_json"))],
        source_kind="promotion",
        source_path=f"memory_promotion_journal:{candidate.get('id', '')}",
        importance=1.0,
    )


def _rows_to_fragments(rows: list[dict[str, object]]) -> list[MemoryFragment]:
    return [
        MemoryFragment(
            title=str(row.get("title", "")),
            body=str(row.get("body", "")),
            ulid=str(row.get("ulid", "")),
            source_kind=str(row.get("source_kind", "")),
            source_path=str(row.get("source_path", "")),
            scope=str(row.get("scope", "")),
            agent_name=str(row.get("agent_name", "")),
            tags=[str(tag) for tag in _list_value(row.get("tags_json"))],
            importance=_safe_float(row.get("importance")),
            created_at=_safe_float(row.get("created_at")),
            updated_at=_safe_float(row.get("updated_at")),
        )
        for row in rows
    ]


def _verify_shared_guardrail(title: str, body: str) -> MemoryPromotionResult | None:
    return _verify_shared_guardrail_text(_render_markdown(title, body))


def _verify_shared_guardrail_text(text: str) -> MemoryPromotionResult | None:
    from ductor_bot.multiagent.shared_knowledge import _audit_shared_memory

    audit = _audit_shared_memory(text)
    if audit.secret_line_numbers:
        return _rejected(
            "shared_secret_like",
            secret_line_count=len(audit.secret_line_numbers),
        )
    if audit.warnings:
        return _needs_review("shared_guardrail_warning", warning_count=len(audit.warnings))
    return None


def _candidate_markdown(candidate: dict[str, object]) -> str:
    return _render_markdown(str(candidate.get("title", "")).strip(), str(candidate.get("body", "")).strip())


def _render_markdown(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def _append_markdown(original: str, addition: str) -> str:
    prefix = original.rstrip()
    if not prefix:
        return addition
    return f"{prefix}\n\n{addition}"


def _find_promoted_ulid(candidate: dict[str, object], fragments: list[MemoryFragment]) -> str:
    title = str(candidate.get("title", ""))
    body = str(candidate.get("body", ""))
    for fragment in fragments:
        if _same_title(fragment.title, title) and _normalize_body(fragment.body) == _normalize_body(body):
            return fragment.ulid
    return ""


def _duplicate_or_no_new_info(
    candidate: MemoryFragment,
    existing: list[MemoryFragment],
) -> str:
    candidate_title = _normalize_title(candidate.title)
    candidate_body = _normalize_body(candidate.body)
    candidate_lines = _body_line_set(candidate.body)
    for fragment in existing:
        if _normalize_title(fragment.title) != candidate_title:
            continue
        existing_body = _normalize_body(fragment.body)
        if existing_body == candidate_body:
            return "duplicate"
        existing_lines = _body_line_set(fragment.body)
        if candidate_lines and candidate_lines.issubset(existing_lines):
            return "no_new_info"
    return ""


def _source_message_ids(candidate: dict[str, object]) -> list[int]:
    return [
        message_id
        for value in _list_value(candidate.get("source_message_ids_json"))
        if (message_id := _safe_int(value)) is not None
    ]


def _agent_name_for_scope(candidate: dict[str, object]) -> str:
    target_scope = str(candidate.get("target_scope", "")).strip()
    if target_scope == "sharedmemory":
        return ""
    return str(candidate.get("agent_name", "")).strip()


def _list_value(value: Any) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_float(value: object) -> float:
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int | None:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return None


def _same_title(left: str, right: str) -> bool:
    return _normalize_title(left) == _normalize_title(right)


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def _normalize_body(body: str) -> str:
    return "\n".join(_body_lines(body))


def _body_lines(body: str) -> tuple[str, ...]:
    return tuple(line.strip().lower() for line in body.splitlines() if line.strip())


def _body_line_set(body: str) -> set[str]:
    return set(_body_lines(body))


def _rejected(reason: str, **metadata: object) -> MemoryPromotionResult:
    return MemoryPromotionResult(
        accepted=False,
        status="rejected",
        metadata={"reason": reason, **metadata},
    )


def _needs_review(reason: str, **metadata: object) -> MemoryPromotionResult:
    return MemoryPromotionResult(
        accepted=False,
        status="needs_review",
        metadata={"reason": reason, **metadata},
    )
