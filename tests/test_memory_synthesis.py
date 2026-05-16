from __future__ import annotations

import json
from pathlib import Path

import pytest

from ductor_bot.cli.types import AgentResponse
from ductor_bot.config import AgentConfig
from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_promotion_journal_repo import (
    MemoryPromotionJournalRepository,
)
from ductor_bot.runtime.state.repositories.message_repo import MessageRepository
from ductor_bot.runtime.state.repositories.session_repo import SessionRepository
from ductor_bot.scripts import memory_synthesis
from ductor_bot.session.manager import ProviderSessionData, SessionData
from ductor_bot.workspace.paths import DuctorPaths


def _seed_session(db_path: Path, *, chat_id: int, provider: str = "gemini", model: str = "auto") -> None:
    db = RuntimeStateDB(db_path)
    session = SessionData(
        chat_id=chat_id,
        transport="tg",
        provider=provider,
        model=model,
        provider_sessions={
            provider: ProviderSessionData(
                session_id="resume-session-123",
                message_count=4,
                total_cost_usd=0.0,
                total_tokens=100,
            )
        },
    )
    SessionRepository(db).upsert(f"tg:{chat_id}", session)


@pytest.mark.asyncio
async def test_run_synthesis_uses_configured_docker_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "ductor-home"
    home.mkdir()
    db_path = home / "state.db"
    _seed_session(db_path, chat_id=123)
    db = RuntimeStateDB(db_path)
    message_id = MessageRepository(db).append("tg:123", "user", "I prefer terse answers.")

    config = AgentConfig(
        ductor_home=str(home),
        state_backend="sqlite",
        state_db_path=str(db_path),
        docker={"enabled": True, "container_name": "ductor-sandbox"},
    )

    seen: dict[str, object] = {}

    class _FakeCLIService:
        async def execute(self, _request: object) -> AgentResponse:
            return AgentResponse(
                result=json.dumps(
                    {
                        "candidates": [
                            {
                                "target_scope": "mainmemory",
                                "title": "Answer style",
                                "body": "- Prefers terse answers.",
                                "tags": ["preference"],
                                "source_message_ids": [message_id],
                            }
                        ]
                    }
                )
            )

    class _FakeOrchestrator:
        def __init__(
            self,
            _config: AgentConfig,
            _paths: DuctorPaths,
            *,
            docker_container: str = "",
            agent_name: str = "main",
            interagent_port: int = 8799,
        ) -> None:
            seen["docker_container"] = docker_container
            seen["agent_name"] = agent_name
            seen["interagent_port"] = interagent_port
            self._cli_service = _FakeCLIService()

    monkeypatch.setattr(memory_synthesis, "Orchestrator", _FakeOrchestrator)

    exit_code = await memory_synthesis.run_synthesis(
        123,
        config=config,
        paths=DuctorPaths(home),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert seen["docker_container"] == "ductor-sandbox"
    assert "Memory synthesis completed for chat_id=123" in output
    assert "created=1 skipped=0" in output
    assert "Prefers terse answers" not in output
    pending = MemoryPromotionJournalRepository(db).list_pending(target_scope="mainmemory")
    assert len(pending) == 1
    assert pending[0]["title"] == "Answer style"


@pytest.mark.asyncio
async def test_run_synthesis_prints_failure_instead_of_success_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "ductor-home"
    home.mkdir()
    db_path = home / "state.db"
    _seed_session(db_path, chat_id=123, model="gemini-3-flash-preview")

    config = AgentConfig(
        ductor_home=str(home),
        state_backend="sqlite",
        state_db_path=str(db_path),
    )

    class _FakeCLIService:
        async def execute(self, _request: object) -> AgentResponse:
            return AgentResponse(result="provider failed", is_error=True, returncode=42)

    class _FakeOrchestrator:
        def __init__(
            self,
            _config: AgentConfig,
            _paths: DuctorPaths,
            *,
            docker_container: str = "",
            agent_name: str = "main",
            interagent_port: int = 8799,
        ) -> None:
            self._cli_service = _FakeCLIService()

    monkeypatch.setattr(memory_synthesis, "Orchestrator", _FakeOrchestrator)

    exit_code = await memory_synthesis.run_synthesis(
        123,
        config=config,
        paths=DuctorPaths(home),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Memory synthesis failed for chat_id=123" in output
    assert "provider failed" not in output
    assert "Memory synthesis completed for chat_id=123" not in output
