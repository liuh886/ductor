
"""Run an explicit memory-synthesis maintenance turn against the latest session."""

from __future__ import annotations

import argparse
import asyncio

from ductor_bot.__main__ import load_config
from ductor_bot.cli.types import AgentRequest
from ductor_bot.config import AgentConfig, resolve_timeout
from ductor_bot.orchestrator.core import Orchestrator
from ductor_bot.runtime.memory.synthesis_producer import (
    build_memory_synthesis_prompt,
    write_synthesis_candidates,
)
from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_promotion_journal_repo import (
    MemoryPromotionJournalRepository,
)
from ductor_bot.runtime.state.repositories.message_repo import MessageRepository
from ductor_bot.runtime.state.repositories.session_repo import SessionRepository
from ductor_bot.session.manager import SessionData
from ductor_bot.workspace.paths import DuctorPaths, resolve_paths


def _find_latest_session(db: RuntimeStateDB, chat_id: int) -> SessionData | None:
    """Return the most recently updated session for ``chat_id``."""
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT storage_key
            FROM sessions
            WHERE chat_id = ?
            ORDER BY updated_at DESC, last_active DESC
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
    if row is None:
        return None
    return SessionRepository(db).get(str(row["storage_key"]))


def _build_request(
    session: SessionData,
    config: AgentConfig,
    limit: int,
    message_repo: MessageRepository | None = None,
) -> AgentRequest:
    """Build the maintenance request for a resumed synthesis turn."""
    prompt, _source_window = build_memory_synthesis_prompt(
        message_repo,
        session.session_key.storage_key,
        limit=limit,
    )
    return AgentRequest(
        prompt=prompt,
        provider_override=session.provider,
        model_override=session.model,
        chat_id=session.chat_id,
        topic_id=session.topic_id,
        process_label=f"memory_synthesis:{session.chat_id}",
        resume_session=session.session_id or None,
        timeout_seconds=resolve_timeout(config, "background"),
    )


async def run_synthesis(
    chat_id: int,
    limit: int = 50,
    *,
    config: AgentConfig | None = None,
    paths: DuctorPaths | None = None,
) -> int:
    """Resume the latest active session for ``chat_id`` and run memory synthesis."""
    resolved_paths = paths or resolve_paths()
    resolved_config = config or load_config()
    db_path = resolved_config.resolved_state_db_path()
    docker_container = (
        resolved_config.docker.container_name if resolved_config.docker.enabled else ""
    )

    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        return 1

    db = RuntimeStateDB(db_path)
    message_repo = MessageRepository(db)
    journal_repo = MemoryPromotionJournalRepository(db)
    session = _find_latest_session(db, chat_id)
    if session is None:
        print(f"No active session found for chat_id={chat_id}.")
        return 1
    if not session.session_id:
        print(f"Latest session for chat_id={chat_id} has no resumable provider session_id.")
        return 1

    orch = Orchestrator(
        resolved_config,
        resolved_paths,
        docker_container=docker_container,
    )
    request = _build_request(session, resolved_config, limit, message_repo)
    _prompt, source_window = build_memory_synthesis_prompt(
        message_repo,
        session.session_key.storage_key,
        limit=limit,
    )
    response = await orch._cli_service.execute(request)

    if response.is_error:
        print(
            f"Memory synthesis failed for chat_id={chat_id} "
            f"(provider={session.provider}, model={session.model}, session={session.session_id})."
        )
        return 1

    summary = write_synthesis_candidates(
        response.result,
        journal_repo=journal_repo,
        session_storage_key=session.session_key.storage_key,
        source_window=source_window,
        agent_name="main",
        producer="memory_synthesis_cli",
    )
    print(
        f"Memory synthesis completed for chat_id={chat_id}: "
        f"created={summary.created} skipped={summary.skipped} error={summary.error}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an explicit memory synthesis turn.")
    parser.add_argument("--chat-id", type=int, required=True, help="The chat ID to analyze.")
    parser.add_argument("--limit", type=int, default=50, help="Recent-message priority hint.")
    args = parser.parse_args()

    raise SystemExit(asyncio.run(run_synthesis(args.chat_id, args.limit)))


if __name__ == "__main__":
    main()
