from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ductor_bot.cli.types import AgentResponse
from ductor_bot.runtime.skills.extractor import SkillExtractor
from ductor_bot.runtime.state import MessageRepository, RuntimeStateDB
from ductor_bot.runtime.state.repositories.session_summary_repo import SessionSummaryRepository
from ductor_bot.tasks.models import TaskEntry


@pytest.mark.asyncio
async def test_skill_extractor_success(tmp_path: Path) -> None:
    """Verify that SkillExtractor generates a discoverable skill directory."""
    skills_dir = tmp_path / "skills"
    db_path = tmp_path / "state.db"
    db = RuntimeStateDB(db_path)
    message_repo = MessageRepository(db)
    summary_repo = SessionSummaryRepository(db)

    cli_service = MagicMock()
    cli_service.execute = AsyncMock()
    cli_service.execute.return_value = AgentResponse(
        result="# Test Skill\n\nContext: test\nProcedural Steps: 1. do test\nKey Findings: test ok\nTools & Commands: test_tool",
        is_error=False,
        num_turns=1,
    )

    extractor = SkillExtractor(
        cli_service=cli_service,
        message_repo=message_repo,
        summary_repo=summary_repo,
        skills_dir=skills_dir,
    )

    entry = TaskEntry(
        task_id="task-123",
        chat_id=1,
        parent_agent="main",
        name="Test Task",
        prompt_preview="test prompt",
        provider="claude",
        model="opus",
        status="done",
        original_prompt="full test prompt",
    )

    message_repo.append("task:task-123", "user", "I want to test")
    message_repo.append("task:task-123", "assistant", "I am testing")

    skill_path = await extractor.extract(entry)

    assert skill_path is not None
    assert skill_path.exists()
    assert skill_path.is_dir()
    assert skill_path.parent == skills_dir
    assert skill_path.name.startswith("skill_Test_Task_task-123")

    skill_md = skill_path / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")
    assert "# Test Skill" in content
    assert "Procedural Steps: 1. do test" in content

    cli_service.execute.assert_called_once()
    args, _ = cli_service.execute.call_args
    request = args[0]
    assert "Test Task" in request.prompt
    assert "full test prompt" in request.prompt
    assert "--- USER ---" in request.prompt
    assert "I want to test" in request.prompt
    assert "--- ASSISTANT ---" in request.prompt
    assert "I am testing" in request.prompt
    assert request.provider_override == "claude"
    assert request.model_override == "opus"


@pytest.mark.asyncio
async def test_skill_extractor_no_history(tmp_path: Path) -> None:
    """Verify that SkillExtractor skips if there is no execution history."""
    skills_dir = tmp_path / "skills"
    db_path = tmp_path / "state.db"
    db = RuntimeStateDB(db_path)
    message_repo = MessageRepository(db)
    summary_repo = SessionSummaryRepository(db)
    cli_service = MagicMock()

    extractor = SkillExtractor(
        cli_service=cli_service,
        message_repo=message_repo,
        summary_repo=summary_repo,
        skills_dir=skills_dir,
    )

    entry = TaskEntry(
        task_id="empty-task",
        chat_id=1,
        parent_agent="main",
        name="Empty",
        prompt_preview="none",
        provider="claude",
        model="opus",
        status="done",
    )

    skill_path = await extractor.extract(entry)

    assert skill_path is None
    cli_service.execute.assert_not_called()
