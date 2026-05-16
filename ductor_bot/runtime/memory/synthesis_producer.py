"""Produce pending memory-promotion candidates from synthesis CLI output."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from ductor_bot.runtime.state.repositories.memory_promotion_journal_repo import (
    MemoryPromotionJournalRepository,
)
from ductor_bot.runtime.state.repositories.message_repo import MessageRepository

ALLOWED_TARGET_SCOPES = frozenset({"mainmemory", "sharedmemory"})
DEFAULT_SOURCE_WINDOW_LIMIT = 20
MAX_TAGS = 8
MAX_TAG_LENGTH = 64
MAX_TITLE_LENGTH = 160
MAX_BODY_LENGTH = 1200
PREVIEW_CHARS = 180


@dataclass(frozen=True, slots=True)
class MemorySynthesisSummary:
    """Small result object for logs, tests, and CLI stdout."""

    created: int = 0
    skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def error(self) -> str:
        """Return a compact error string for call sites that expect one field."""
        return "; ".join(self.errors)


@dataclass(frozen=True, slots=True)
class _NormalizedCandidate:
    target_scope: str
    title: str
    body: str
    tags: tuple[str, ...]
    source_message_ids: tuple[int, ...]


def recent_source_window(
    message_repo: MessageRepository | None,
    session_storage_key: str,
    *,
    limit: int = DEFAULT_SOURCE_WINDOW_LIMIT,
) -> list[dict[str, object]]:
    """Return the recent message evidence window in chronological order."""
    if message_repo is None:
        return []
    messages = message_repo.list_by_session(session_storage_key)
    return messages[-max(1, limit) :]


def render_memory_synthesis_prompt(source_window: Sequence[Mapping[str, object]]) -> str:
    """Render the JSON-only memory candidate extraction prompt."""
    evidence = "\n".join(_render_message_evidence(row) for row in source_window)
    if not evidence:
        evidence = "- No runtime message evidence is available. Return an empty candidates array."

    return (
        "SYSTEM INSTRUCTION: You are producing pending memory candidates.\n"
        "Return exactly one JSON object and no markdown, explanation, or tool calls.\n"
        'The object schema is: {"candidates":[{"target_scope":"mainmemory|sharedmemory",'
        '"title":"...","body":"- ...","tags":["preference"],'
        '"source_message_ids":[123]}]}.\n\n'
        "Write candidates for memory_promotion_journal only. Do not edit MAINMEMORY.md, "
        "SHAREDMEMORY.md, memory fragments, or workspace files.\n"
        "Use target_scope=mainmemory for facts scoped to this agent, and sharedmemory for "
        "cross-agent reusable facts.\n"
        "Each candidate must cite one or more source_message_ids from the evidence window.\n"
        "Keep title and body concise; body may be markdown bullets. Use a small tags array.\n\n"
        "Evidence window:\n"
        f"{evidence}"
    )


def build_memory_synthesis_prompt(
    message_repo: MessageRepository | None,
    session_storage_key: str,
    *,
    limit: int = DEFAULT_SOURCE_WINDOW_LIMIT,
) -> tuple[str, list[dict[str, object]]]:
    """Build a prompt and source window from recent persisted messages."""
    source_window = recent_source_window(message_repo, session_storage_key, limit=limit)
    return render_memory_synthesis_prompt(source_window), source_window


def write_synthesis_candidates(  # noqa: PLR0913
    response_text: str,
    *,
    journal_repo: MemoryPromotionJournalRepository | None,
    session_storage_key: str,
    source_window: Sequence[Mapping[str, object]],
    agent_name: str = "main",
    producer: str = "memory_synthesis",
) -> MemorySynthesisSummary:
    """Parse a JSON envelope and write valid candidates to the promotion journal."""
    if journal_repo is None:
        return MemorySynthesisSummary(skipped=1, errors=("memory promotion journal unavailable",))

    envelope = _parse_envelope(response_text)
    if envelope is None:
        return MemorySynthesisSummary(skipped=1, errors=("malformed JSON envelope",))

    candidates = envelope.get("candidates")
    if not isinstance(candidates, list):
        return MemorySynthesisSummary(skipped=1, errors=("missing candidates array",))

    allowed_ids: set[int] = set()
    for row in source_window:
        message_id = _coerce_int(row.get("id"))
        if message_id is not None:
            allowed_ids.add(message_id)
    created = 0
    skipped = 0
    errors: list[str] = []
    for index, raw_candidate in enumerate(candidates):
        candidate = _normalize_candidate(raw_candidate, allowed_ids)
        if candidate is None:
            skipped += 1
            errors.append(f"candidate {index} rejected")
            continue

        journal_repo.create_candidate(
            session_storage_key=session_storage_key,
            source_message_ids=candidate.source_message_ids,
            agent_name=agent_name,
            target_scope=candidate.target_scope,
            title=candidate.title,
            body=candidate.body,
            tags=candidate.tags,
            verification={
                "producer": producer,
                "candidate_index": index,
                "result_chars": len(response_text),
                "source_window_size": len(source_window),
            },
        )
        created += 1

    return MemorySynthesisSummary(created=created, skipped=skipped, errors=tuple(errors))


def _parse_envelope(response_text: str) -> dict[str, object] | None:
    text = response_text.strip()
    if not text:
        return None
    json_text = _extract_json_object(text)
    if json_text is None:
        return None
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_json_object(text: str) -> str | None:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return None
    candidate = text[first : last + 1].strip()
    if "{" in text[:first] or "}" in text[last + 1 :]:
        return None
    return candidate


def _normalize_candidate(
    raw_candidate: object,
    allowed_ids: set[int],
) -> _NormalizedCandidate | None:
    if not isinstance(raw_candidate, dict):
        return None

    target_scope = str(raw_candidate.get("target_scope", "")).strip().lower()
    title = str(raw_candidate.get("title", "")).strip()
    body = str(raw_candidate.get("body", "")).strip()
    if target_scope not in ALLOWED_TARGET_SCOPES or not title or not body:
        return None
    if len(title) > MAX_TITLE_LENGTH or len(body) > MAX_BODY_LENGTH:
        return None

    source_ids = _valid_source_ids(raw_candidate.get("source_message_ids"), allowed_ids)
    if not source_ids:
        return None

    return _NormalizedCandidate(
        target_scope=target_scope,
        title=title,
        body=body,
        tags=tuple(_normalize_tags(raw_candidate.get("tags"))),
        source_message_ids=tuple(source_ids),
    )


def _valid_source_ids(raw_ids: object, allowed_ids: set[int]) -> list[int]:
    if not isinstance(raw_ids, list):
        return []
    valid: list[int] = []
    for raw_id in raw_ids:
        try:
            message_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if message_id in allowed_ids and message_id not in valid:
            valid.append(message_id)
    return valid


def _coerce_int(value: object) -> int | None:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return None


def _normalize_tags(raw_tags: object) -> list[str]:
    if not isinstance(raw_tags, list):
        return []
    tags: list[str] = []
    for raw_tag in raw_tags:
        tag = str(raw_tag).strip().lower()
        if not tag or len(tag) > MAX_TAG_LENGTH or tag in tags:
            continue
        tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def _render_message_evidence(row: Mapping[str, object]) -> str:
    message_id = row.get("id", "")
    role = str(row.get("role", "")).strip() or "unknown"
    content = _preview(str(row.get("content_text", "") or row.get("thought", "") or ""))
    return f"- id={message_id} role={role} preview={json.dumps(content, ensure_ascii=False)}"


def _preview(content: str) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= PREVIEW_CHARS:
        return normalized
    return normalized[: PREVIEW_CHARS - 1].rstrip() + "..."
