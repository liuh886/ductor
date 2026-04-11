"""Task repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import time

from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.tasks.models import TaskEntry


class TaskRepository:
    """CRUD for task metadata."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def replace_all(self, tasks: list[TaskEntry]) -> None:
        """Replace the persisted task set."""
        now = time.time()
        with self._db.connect() as conn:
            conn.execute("DELETE FROM tasks")
            for task in tasks:
                payload = task.to_dict()
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, chat_id, parent_agent, name, prompt_preview,
                        provider, model, status, session_id, created_at,
                        completed_at, elapsed_seconds, error, result_preview,
                        question_count, num_turns, last_question, original_prompt,
                        thinking, tasks_dir, thread_id, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.chat_id,
                        task.parent_agent,
                        task.name,
                        task.prompt_preview,
                        task.provider,
                        task.model,
                        task.status,
                        task.session_id,
                        task.created_at,
                        task.completed_at,
                        task.elapsed_seconds,
                        task.error,
                        task.result_preview,
                        task.question_count,
                        task.num_turns,
                        task.last_question,
                        task.original_prompt,
                        task.thinking,
                        task.tasks_dir,
                        task.thread_id,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )

    def list_all(self) -> list[TaskEntry]:
        """Load all tasks from the DB."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM tasks ORDER BY created_at DESC").fetchall()
        return [TaskEntry.from_dict(json.loads(str(row["payload_json"]))) for row in rows]
