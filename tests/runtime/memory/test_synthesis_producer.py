from __future__ import annotations

import json
from pathlib import Path

from ductor_bot.runtime.memory.synthesis_producer import (
    build_memory_synthesis_prompt,
    write_synthesis_candidates,
)
from ductor_bot.runtime.state import (
    MemoryPromotionJournalRepository,
    MessageRepository,
    RuntimeStateDB,
)


def _repos(tmp_path: Path) -> tuple[MemoryPromotionJournalRepository, MessageRepository]:
    db = RuntimeStateDB(tmp_path / "state.db")
    return MemoryPromotionJournalRepository(db), MessageRepository(db)


def test_valid_json_creates_pending_journal_row(tmp_path: Path) -> None:
    journal, messages = _repos(tmp_path)
    message_id = messages.append("tg:1", "user", "I prefer concise answers.")
    _prompt, source_window = build_memory_synthesis_prompt(messages, "tg:1", limit=20)

    summary = write_synthesis_candidates(
        json.dumps(
            {
                "candidates": [
                    {
                        "target_scope": "mainmemory",
                        "title": "Answer style",
                        "body": "- Prefers concise answers.",
                        "tags": ["preference"],
                        "source_message_ids": [message_id],
                    }
                ]
            }
        ),
        journal_repo=journal,
        session_storage_key="tg:1",
        source_window=source_window,
    )

    pending = journal.list_pending(target_scope="mainmemory", agent_name="main")
    assert summary.created == 1
    assert summary.skipped == 0
    assert len(pending) == 1
    assert pending[0]["title"] == "Answer style"
    assert pending[0]["source_message_ids_json"] == [message_id]
    assert pending[0]["tags_json"] == ["preference"]
    assert pending[0]["verification_json"]["producer"] == "memory_synthesis"
    assert "result" not in pending[0]["verification_json"]
    assert "prompt" not in pending[0]["verification_json"]


def test_malformed_json_creates_no_journal_rows(tmp_path: Path) -> None:
    journal, messages = _repos(tmp_path)
    messages.append("tg:1", "user", "Remember this.")
    _prompt, source_window = build_memory_synthesis_prompt(messages, "tg:1")

    summary = write_synthesis_candidates(
        "not json",
        journal_repo=journal,
        session_storage_key="tg:1",
        source_window=source_window,
    )

    assert summary.created == 0
    assert summary.skipped == 1
    assert journal.list_pending() == []


def test_wrapped_json_envelope_is_extracted(tmp_path: Path) -> None:
    journal, messages = _repos(tmp_path)
    message_id = messages.append("tg:1", "user", "I prefer direct answers.")
    _prompt, source_window = build_memory_synthesis_prompt(messages, "tg:1")

    summary = write_synthesis_candidates(
        "```json\n"
        + json.dumps(
            {
                "candidates": [
                    {
                        "target_scope": "mainmemory",
                        "title": "Answer style",
                        "body": "- Prefers direct answers.",
                        "source_message_ids": [message_id],
                    }
                ]
            }
        )
        + "\n```",
        journal_repo=journal,
        session_storage_key="tg:1",
        source_window=source_window,
    )

    assert summary.created == 1
    assert journal.list_pending()[0]["title"] == "Answer style"


def test_invalid_source_ids_are_rejected(tmp_path: Path) -> None:
    journal, messages = _repos(tmp_path)
    messages.append("tg:1", "user", "Only this ID is valid.")
    _prompt, source_window = build_memory_synthesis_prompt(messages, "tg:1")

    summary = write_synthesis_candidates(
        json.dumps(
            {
                "candidates": [
                    {
                        "target_scope": "mainmemory",
                        "title": "Bad source",
                        "body": "- Unsupported claim.",
                        "tags": ["preference"],
                        "source_message_ids": [9999],
                    }
                ]
            }
        ),
        journal_repo=journal,
        session_storage_key="tg:1",
        source_window=source_window,
    )

    assert summary.created == 0
    assert summary.skipped == 1
    assert journal.list_pending() == []


def test_overlong_candidate_is_rejected(tmp_path: Path) -> None:
    journal, messages = _repos(tmp_path)
    message_id = messages.append("tg:1", "user", "Remember this compact fact.")
    _prompt, source_window = build_memory_synthesis_prompt(messages, "tg:1")

    summary = write_synthesis_candidates(
        json.dumps(
            {
                "candidates": [
                    {
                        "target_scope": "mainmemory",
                        "title": "Too long",
                        "body": "- " + ("x" * 1300),
                        "source_message_ids": [message_id],
                    }
                ]
            }
        ),
        journal_repo=journal,
        session_storage_key="tg:1",
        source_window=source_window,
    )

    assert summary.created == 0
    assert summary.skipped == 1
    assert journal.list_pending() == []


def test_shared_scope_candidate_is_accepted(tmp_path: Path) -> None:
    journal, messages = _repos(tmp_path)
    message_id = messages.append("tg:1", "assistant", "The shared alias @repo points at D:/repo.")
    _prompt, source_window = build_memory_synthesis_prompt(messages, "tg:1")

    summary = write_synthesis_candidates(
        json.dumps(
            {
                "candidates": [
                    {
                        "target_scope": "sharedmemory",
                        "title": "Shared repo alias",
                        "body": "- @repo maps to D:/repo.",
                        "tags": ["alias"],
                        "source_message_ids": [message_id],
                    }
                ]
            }
        ),
        journal_repo=journal,
        session_storage_key="tg:1",
        source_window=source_window,
    )

    pending = journal.list_pending(target_scope="sharedmemory", agent_name="main")
    assert summary.created == 1
    assert len(pending) == 1
