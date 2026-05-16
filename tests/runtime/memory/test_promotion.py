from __future__ import annotations

from pathlib import Path

import pytest

from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.runtime.memory.promotion import promote_memory_candidate, verify_memory_candidate
from ductor_bot.runtime.state import (
    MemoryFragmentRepository,
    MemoryPromotionJournalRepository,
    MessageRepository,
    RuntimeStateDB,
)


def _repos(tmp_path: Path) -> tuple[
    MemoryPromotionJournalRepository,
    MessageRepository,
    MemoryFragmentRepository,
]:
    db = RuntimeStateDB(tmp_path / "state.db")
    return (
        MemoryPromotionJournalRepository(db),
        MessageRepository(db),
        MemoryFragmentRepository(db),
    )


def _candidate(
    journal: MemoryPromotionJournalRepository,
    messages: MessageRepository,
    *,
    session_storage_key: str = "tg:1",
    target_scope: str = "mainmemory",
    title: str = "Preference",
    body: str = "- prefers concise answers",
    agent_name: str = "main",
) -> int:
    message_id = messages.append(session_storage_key, "assistant", "noted")
    return journal.create_candidate(
        session_storage_key=session_storage_key,
        source_message_ids=[message_id],
        agent_name=agent_name,
        target_scope=target_scope,
        title=title,
        body=body,
    )


def test_verify_accepts_pending_candidate(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    candidate_id = _candidate(journal, messages)
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is True
    assert result.status == "promoted"
    assert result.metadata["reason"] == "accepted"


def test_verify_rejects_missing_source_message(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    candidate_id = journal.create_candidate(
        session_storage_key="tg:1",
        source_message_ids=[404],
        agent_name="main",
        target_scope="mainmemory",
        title="Preference",
        body="- prefers concise answers",
    )
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is False
    assert result.status == "rejected"
    assert result.metadata["reason"] == "missing_source_message"


def test_verify_rejects_source_message_session_mismatch(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    message_id = messages.append("tg:2", "assistant", "noted")
    candidate_id = journal.create_candidate(
        session_storage_key="tg:1",
        source_message_ids=[message_id],
        agent_name="main",
        target_scope="mainmemory",
        title="Preference",
        body="- prefers concise answers",
    )
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is False
    assert result.status == "rejected"
    assert result.metadata["reason"] == "session_mismatch"


def test_verify_rejects_empty_source_evidence(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    message_id = messages.append("tg:1", "assistant", "")
    candidate_id = journal.create_candidate(
        session_storage_key="tg:1",
        source_message_ids=[message_id],
        agent_name="main",
        target_scope="mainmemory",
        title="Preference",
        body="- prefers concise answers",
    )
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is False
    assert result.status == "rejected"
    assert result.metadata["reason"] == "empty_source_evidence"


def test_verify_rejects_sharedmemory_secret_like_content(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    candidate_id = _candidate(
        journal,
        messages,
        target_scope="sharedmemory",
        title="Credentials",
        body="- api_key = sk-abcdef1234567890",
        agent_name="",
    )
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is False
    assert result.status == "rejected"
    assert result.metadata["reason"] == "shared_secret_like"


def test_verify_rejects_conflict(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    fragments.replace_for_scope(
        "mainmemory",
        [
            MemoryFragment(
                title="Preference",
                body="- prefers concise answers",
                scope="mainmemory",
                agent_name="main",
            )
        ],
        agent_name="main",
    )
    candidate_id = _candidate(journal, messages, body="- prefers exhaustive answers")
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is False
    assert result.status == "rejected"
    assert result.metadata["reason"] == "conflict"


def test_promote_mainmemory_appends_markdown_and_syncs_fragments(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    candidate_id = _candidate(journal, messages)
    mainmemory = tmp_path / "MAINMEMORY.md"
    mainmemory.write_text("# Main Memory\n", encoding="utf-8")

    result = promote_memory_candidate(
        candidate_id,
        journal,
        messages,
        fragments,
        mainmemory_path=mainmemory,
        sharedmemory_path=tmp_path / "SHAREDMEMORY.md",
    )

    assert result.accepted is True
    assert "## Preference" in mainmemory.read_text(encoding="utf-8")
    stored = fragments.list_by_scope("mainmemory", agent_name="main")
    assert [row["title"] for row in stored] == ["Preference"]
    loaded = journal.get(candidate_id)
    assert loaded is not None
    assert loaded["status"] == "promoted"
    assert loaded["promoted_fragment_ulid"]
    assert loaded["verification_json"]["reason"] == "promoted"


def test_promote_sharedmemory_appends_markdown_and_syncs_fragments(tmp_path: Path) -> None:
    journal, messages, fragments = _repos(tmp_path)
    candidate_id = _candidate(
        journal,
        messages,
        target_scope="sharedmemory",
        title="Deploy Alert",
        body="- staging deploy window is Friday",
        agent_name="",
    )
    sharedmemory = tmp_path / "SHAREDMEMORY.md"
    sharedmemory.write_text("# Shared Knowledge\n", encoding="utf-8")

    result = promote_memory_candidate(
        candidate_id,
        journal,
        messages,
        fragments,
        mainmemory_path=tmp_path / "MAINMEMORY.md",
        sharedmemory_path=sharedmemory,
    )

    assert result.accepted is True
    assert "## Deploy Alert" in sharedmemory.read_text(encoding="utf-8")
    stored = fragments.list_by_scope("sharedmemory")
    assert [row["title"] for row in stored] == ["Deploy Alert"]
    loaded = journal.get(candidate_id)
    assert loaded is not None
    assert loaded["status"] == "promoted"


@pytest.mark.parametrize(
    ("candidate_body", "expected_reason"),
    [
        ("- prefers concise answers", "duplicate"),
        ("- prefers concise answers", "no_new_info"),
    ],
)
def test_verify_rejects_idempotent_duplicate_or_no_new_info(
    tmp_path: Path,
    candidate_body: str,
    expected_reason: str,
) -> None:
    journal, messages, fragments = _repos(tmp_path)
    existing_body = (
        "- prefers concise answers"
        if expected_reason == "duplicate"
        else "- prefers concise answers\n- likes bullet lists"
    )
    fragments.replace_for_scope(
        "mainmemory",
        [
            MemoryFragment(
                title="Preference",
                body=existing_body,
                scope="mainmemory",
                agent_name="main",
            )
        ],
        agent_name="main",
    )
    candidate_id = _candidate(journal, messages, body=candidate_body)
    candidate = journal.get(candidate_id)

    result = verify_memory_candidate(candidate, messages, fragments)

    assert result.accepted is False
    assert result.status == "rejected"
    assert result.metadata["reason"] == expected_reason
