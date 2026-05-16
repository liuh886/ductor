"""TaskHub: central coordinator for background task delegation."""

# ruff: noqa: PLR0913, PLR0915

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.runtime.compression.context_compressor import ContextCompressor
from ductor_bot.runtime.compression.summary_selector import SummarySelector
from ductor_bot.runtime.memory import extract_markdown_fragments
from ductor_bot.runtime.skills.extractor import SkillExtractor
from ductor_bot.runtime.state.repositories.session_summary_repo import SessionSummaryRepository
from ductor_bot.tasks.models import (
    TaskEntry,
    TaskInFlight,
    TaskResult,
    TaskSubmit,
    normalise_priority,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ductor_bot.cli.process_registry import ProcessRegistry
    from ductor_bot.cli.service import CLIService
    from ductor_bot.config import TasksConfig
    from ductor_bot.runtime.state import MessageRepository, ProcessRepository
    from ductor_bot.tasks.registry import TaskRegistry
    from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)

_FINISHED = frozenset({"done", "failed", "cancelled"})
_RESUMABLE = frozenset({"done", "failed", "cancelled", "waiting"})
_MAINTENANCE_INTERVAL = 5 * 3600  # 5 hours

TaskResultCallback = Callable[[TaskResult], Awaitable[None]]
QuestionHandler = Callable[[str, str, str, int, int | None], Awaitable[None]]
# QuestionHandler(task_id, question, prompt_preview, chat_id, thread_id) -> None

TASK_PROMPT_SUFFIX = """

---
TASK RULES (MANDATORY):
You are a background task agent. You have NO direct user access.

IMPORTANT — If you need ANY information to complete this task (missing details,
clarifications, preferences), you MUST use this tool:
```
python3 tools/task_tools/ask_parent.py "your question here"
```
Do NOT include questions in your response text. The tool forwards your question
to the parent agent who will resume you with the answer.

After finishing, update your task memory: {taskmemory_path}
"""

_RESUME_REMINDER = """

---
REMINDER: You are a background task agent with NO direct user access.
- Need more info? Use: python3 tools/task_tools/ask_parent.py "question"
- Do NOT put questions in your response — the user cannot see them.
- When done, write your final results to: {taskmemory_path}
"""


class TaskHub:
    """Central coordinator for background task delegation.

    Combines ``BackgroundObserver`` execution pattern with ``InterAgentBus``
    result-delivery pattern. Manages the full lifecycle: submit → execute →
    question handling → result delivery.
    """

    def __init__(
        self,
        registry: TaskRegistry,
        paths: DuctorPaths,
        *,
        cli_service: CLIService | None = None,
        process_repo: ProcessRepository | None = None,
        message_repo: MessageRepository | None = None,
        config: TasksConfig,
        process_registry: ProcessRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._paths = paths
        self._cli_service = cli_service
        self._process_repo = process_repo
        self._message_repo = message_repo
        self._cli_services: dict[str, CLIService] = {}
        self._agent_tasks_dirs: dict[str, Path] = {}
        self._config = config
        self._in_flight: dict[str, TaskInFlight] = {}
        self._result_handlers: dict[str, TaskResultCallback] = {}
        self._question_handlers: dict[str, QuestionHandler] = {}
        self._agent_chat_ids: dict[str, int] = {}
        self._maintenance_task: asyncio.Task[None] | None = None
        self._resume_context_compressor: ContextCompressor | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._summary_repo: SessionSummaryRepository | None = None
        if self._message_repo:
            db = getattr(self._message_repo, "_db", None)
            if db:
                self._summary_repo = SessionSummaryRepository(db)
        # #92: registry used to kill task subprocess trees on cancel. A single
        # shared registry works when all task subprocesses register into it
        # (supervisor wires this — see ``AgentSupervisor._wire_task_hub``).
        # For multi-agent setups where each agent owns its own ProcessRegistry,
        # per-agent lookups take precedence via ``_agent_process_registries``.
        self._process_registry = process_registry
        self._agent_process_registries: dict[str, ProcessRegistry] = {}

    def start_maintenance(self) -> None:
        """Start periodic orphan cleanup (call once after bot startup)."""
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(
                self._maintenance_loop(), name="task-maintenance"
            )

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    def set_result_handler(self, agent_name: str, handler: TaskResultCallback) -> None:
        """Register callback for delivering results to a parent agent."""
        self._result_handlers[agent_name] = handler

    def set_question_handler(self, agent_name: str, handler: QuestionHandler) -> None:
        """Register handler for task-agent questions (ask_parent)."""
        self._question_handlers[agent_name] = handler

    def set_cli_service(self, agent_name: str, cli: CLIService) -> None:
        """Register a per-agent CLI service for task execution."""
        self._cli_services[agent_name] = cli

    def set_agent_process_registry(
        self, agent_name: str, process_registry: ProcessRegistry
    ) -> None:
        """Register a per-agent ProcessRegistry for subprocess-aware cancel.

        Each agent's orchestrator owns its own ``ProcessRegistry`` (see
        ``Orchestrator.__init__``), and task subprocesses register under the
        label ``task:<id>`` in THAT registry. When cancel() fires for a task
        we therefore need the registry tied to the task's parent agent; this
        map provides that lookup. Falls back to the shared ``_process_registry``
        when no per-agent entry exists.
        """
        self._agent_process_registries[agent_name] = process_registry

    def _resolve_process_registry(self, parent_agent: str | None) -> ProcessRegistry | None:
        """Pick the ProcessRegistry for *parent_agent*, or the shared default."""
        if parent_agent and parent_agent in self._agent_process_registries:
            return self._agent_process_registries[parent_agent]
        return self._process_registry

    def set_agent_paths(self, agent_name: str, paths: DuctorPaths) -> None:
        """Register per-agent paths for task folder isolation."""
        self._agent_tasks_dirs[agent_name] = paths.tasks_dir

    def set_agent_chat_id(self, agent_name: str, chat_id: int) -> None:
        """Register the primary chat_id for an agent (for resolving CLI-submitted tasks)."""
        self._agent_chat_ids[agent_name] = chat_id

    def _check_enabled(self) -> None:
        if not self._config.enabled:
            msg = "Task system is disabled"
            raise ValueError(msg)
        if self._cli_service is None and not self._cli_services:
            msg = "CLIService not available"
            raise ValueError(msg)

    def submit(self, submit: TaskSubmit) -> str:
        """Create a task, spawn CLI subprocess. Returns task_id."""
        self._check_enabled()

        # Resolve chat_id: CLI subprocess doesn't know it, look up from agent name
        if not submit.chat_id:
            resolved = self._agent_chat_ids.get(submit.parent_agent, 0)
            if resolved:
                submit.chat_id = resolved

        # #79: interactive tasks bypass the per-chat concurrency cap so
        # direct user follow-ups stay responsive under heavy batch load.
        # Active count excludes already-running interactive tasks for the
        # same reason — they never "fill up" the cap for background work.
        priority = normalise_priority(submit.priority)
        if priority != "interactive":
            active = sum(
                1
                for t in self._in_flight.values()
                if t.entry.chat_id == submit.chat_id
                and t.asyncio_task
                and not t.asyncio_task.done()
                and t.entry.priority != "interactive"
            )
            if active >= self._config.max_parallel:
                msg = f"Too many background tasks ({self._config.max_parallel} max)"
                raise ValueError(msg)

        provider = submit.provider_override or ""
        model = submit.model_override or ""
        thinking = submit.thinking_override or ""

        # Resolve per-agent tasks_dir for folder isolation
        agent_tasks_dir = self._agent_tasks_dirs.get(submit.parent_agent)
        entry = self._registry.create(
            submit,
            provider,
            model,
            thinking=thinking,
            tasks_dir=agent_tasks_dir,
            priority=priority,
        )

        # Build prompt with mandatory suffix
        taskmemory = self._registry.taskmemory_path(entry.task_id)
        full_prompt = submit.prompt + TASK_PROMPT_SUFFIX.format(taskmemory_path=taskmemory)

        self._spawn(entry, full_prompt, thinking)

        logger.info(
            "Task submitted id=%s name='%s' parent=%s provider=%s",
            entry.task_id,
            entry.name,
            submit.parent_agent,
            entry.provider or "(parent default)",
        )
        return entry.task_id

    def resume(self, task_id: str, follow_up: str, *, parent_agent: str = "") -> str:
        """Resume a completed task's CLI session with a follow-up. Returns task_id."""
        self._check_enabled()

        entry = self._registry.get(task_id)
        if entry is None:
            msg = f"Task '{task_id}' not found"
            raise ValueError(msg)
        if entry.status not in _RESUMABLE:
            msg = f"Task '{task_id}' is still {entry.status}"
            raise ValueError(msg)
        if not entry.session_id:
            msg = f"Task '{task_id}' has no resumable session"
            raise ValueError(msg)
        if not entry.provider:
            msg = f"Task '{task_id}' has no provider recorded"
            raise ValueError(msg)

        # Reset to running — same entry, same folder, same task_id
        self._registry.update_status(
            task_id,
            "running",
            completed_at=0.0,
            error="",
            result_preview="",
            last_question="",
        )

        # Append a short system reminder so the task agent remembers how to
        # communicate (ask_parent, TASKMEMORY, no direct user access).
        taskmemory = self._registry.taskmemory_path(entry.task_id)
        full_prompt = self._build_resume_prompt(entry.task_id, follow_up, taskmemory)
        self._spawn(
            entry,
            full_prompt,
            entry.thinking,
            resume_session=entry.session_id,
            message_source="task_resume",
        )

        logger.info(
            "Task resumed id=%s name='%s' provider=%s",
            task_id,
            entry.name,
            entry.provider,
        )
        return task_id

    def _build_resume_prompt(self, task_id: str, follow_up: str, taskmemory: Path) -> str:
        """Build the task-resume prompt with optional compressed runtime context."""
        prompt = follow_up + _RESUME_REMINDER.format(taskmemory_path=taskmemory)
        prefix = self._build_resume_context_prefix(task_id)
        if not prefix:
            return prompt
        return f"{prefix}\n\n{prompt}"

    def _build_resume_context_prefix(self, task_id: str) -> str:
        """Return a compressed runtime-context prefix for a resumed task."""
        compressor = self._get_resume_context_compressor()
        if compressor is None:
            return ""
        return compressor.build_prompt_prefix(self._task_session_key(task_id))

    def _get_resume_context_compressor(self) -> ContextCompressor | None:
        """Build and cache a context compressor from the runtime message store."""
        if self._resume_context_compressor is not None:
            return self._resume_context_compressor
        if self._message_repo is None:
            return None
        db = getattr(self._message_repo, "_db", None)
        if db is None:
            return None
        summary_repo = SessionSummaryRepository(db)
        selector = SummarySelector(self._message_repo, summary_repo)
        self._resume_context_compressor = ContextCompressor(selector)
        return self._resume_context_compressor

    def _spawn(
        self,
        entry: TaskEntry,
        prompt: str,
        thinking: str,
        *,
        resume_session: str | None = None,
        message_source: str = "task_submit",
    ) -> None:
        """Create the asyncio task and register it in-flight."""
        inflight = TaskInFlight(entry=entry)
        atask = asyncio.create_task(
            self._run(
                entry,
                prompt,
                thinking,
                resume_session=resume_session,
                message_source=message_source,
            ),
            name=f"task:{entry.task_id}",
        )
        inflight.asyncio_task = atask
        atask.add_done_callback(lambda _: self._in_flight.pop(entry.task_id, None))
        self._in_flight[entry.task_id] = inflight

    async def forward_question(self, task_id: str, question: str) -> str:
        """Forward a task agent's question to the parent. Returns immediately.

        The question is delivered asynchronously to the parent agent's Telegram
        chat. The parent answers by resuming the task with ``resume_task.py``.
        """
        entry = self._registry.get(task_id)
        if entry is None:
            return "Error: Task not found"

        handler = self._question_handlers.get(entry.parent_agent)
        if handler is None:
            return f"Error: No question handler for agent '{entry.parent_agent}'"

        logger.info(
            "Task %s forwarding question to '%s': %s",
            task_id,
            entry.parent_agent,
            question[:80],
        )

        self._registry.update_status(
            task_id,
            entry.status,
            question_count=entry.question_count + 1,
            last_question=question[:200],
        )

        # Mark in-flight task so _run() uses "waiting" instead of "done"
        inflight = self._in_flight.get(task_id)
        if inflight:
            inflight.has_pending_question = True

        # Fire-and-forget: deliver to parent's Telegram chat
        task = asyncio.create_task(
            self._deliver_question(handler, entry, question),
            name=f"task-question:{task_id}",
        )
        task.add_done_callback(lambda _: None)  # prevent GC of fire-and-forget task

        return (
            "Question forwarded to parent agent. "
            "Finish your current work — you will be resumed with the answer."
        )

    async def _deliver_question(
        self,
        handler: QuestionHandler,
        entry: TaskEntry,
        question: str,
    ) -> None:
        """Deliver question to parent agent (background coroutine)."""
        try:
            await handler(
                entry.task_id,
                question,
                entry.prompt_preview,
                entry.chat_id,
                entry.thread_id,
            )
        except Exception:
            logger.exception("Question delivery failed for task %s", entry.task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled.

        Kill order (per issue #92 / Pitfall 2): subprocess tree first so the
        CLI's streaming ``await`` unblocks, THEN asyncio task. Inverting this
        order hangs — ``cli.execute`` is blocked on the subprocess pipe, and
        a pending ``CancelledError`` cannot propagate until the pipe closes.
        """
        inflight = self._in_flight.get(task_id)
        if inflight is None or inflight.asyncio_task is None or inflight.asyncio_task.done():
            return False
        registry = self._resolve_process_registry(inflight.entry.parent_agent)
        if registry is not None:
            await registry.kill_for_task(task_id)
        inflight.asyncio_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await inflight.asyncio_task
        return True

    async def cancel_all(self, chat_id: int) -> int:
        """Cancel all running tasks for a chat.

        Kill order mirrors :meth:`cancel`: every task's subprocess tree is
        killed first (one ``kill_for_task`` per task) before any asyncio
        ``Task.cancel`` fires. Sequential ``await`` keeps each SIGTERM→SIGKILL
        ladder independent (see threat T-02-03).
        """
        targets: list[tuple[str, str | None, asyncio.Task[None]]] = [
            (inflight.entry.task_id, inflight.entry.parent_agent, inflight.asyncio_task)
            for inflight in list(self._in_flight.values())
            if (
                inflight.entry.chat_id == chat_id
                and inflight.asyncio_task
                and not inflight.asyncio_task.done()
            )
        ]
        if not targets:
            return 0
        for task_id, parent_agent, _ in targets:
            registry = self._resolve_process_registry(parent_agent)
            if registry is not None:
                await registry.kill_for_task(task_id)
        cancelled: list[asyncio.Task[None]] = [atask for _, _, atask in targets]
        for atask in cancelled:
            atask.cancel()
        await asyncio.gather(*cancelled, return_exceptions=True)
        return len(cancelled)

    def active_tasks(self, chat_id: int | None = None) -> list[TaskEntry]:
        """Return in-flight task entries."""
        entries = [
            t.entry
            for t in self._in_flight.values()
            if t.asyncio_task and not t.asyncio_task.done()
        ]
        if chat_id is not None:
            entries = [e for e in entries if e.chat_id == chat_id]
        return entries

    async def shutdown(self) -> None:
        """Cancel all in-flight tasks and clean up."""
        if self._maintenance_task and not self._maintenance_task.done():
            self._maintenance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance_task
            self._maintenance_task = None

        cancelled: list[asyncio.Task[None]] = []
        for inflight in list(self._in_flight.values()):
            if inflight.asyncio_task and not inflight.asyncio_task.done():
                inflight.asyncio_task.cancel()
                cancelled.append(inflight.asyncio_task)
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        if self._background_tasks:
            background = list(self._background_tasks)
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            self._background_tasks.clear()
        self._in_flight.clear()

    async def _maintenance_loop(self) -> None:
        """Periodically clean orphaned task entries/folders (every 5 hours)."""
        try:
            while True:
                await asyncio.sleep(_MAINTENANCE_INTERVAL)
                try:
                    removed = self._registry.cleanup_orphans()
                    if removed:
                        logger.info("Task maintenance: removed %d orphan(s)", removed)
                except Exception:
                    logger.exception("Task maintenance failed (continuing)")
        except asyncio.CancelledError:
            pass

    async def _run(
        self,
        entry: TaskEntry,
        prompt: str,
        thinking: str,
        *,
        resume_session: str | None = None,
        message_source: str = "task_submit",
    ) -> None:
        """Execute task as CLI subprocess."""
        from ductor_bot.cli.types import AgentRequest

        cli = self._cli_services.get(entry.parent_agent) or self._cli_service
        assert cli is not None

        t0 = time.monotonic()
        process_id: int | None = None
        process_exit_code: int | None = None
        try:
            timeout = self._config.timeout_seconds

            request = AgentRequest(
                prompt=prompt,
                model_override=entry.model or None,
                provider_override=entry.provider or None,
                chat_id=entry.chat_id,
                topic_id=entry.thread_id,
                process_label=f"task:{entry.task_id}",
                timeout_seconds=timeout,
                resume_session=resume_session,
            )

            # Pre-resolve effective provider/model so the entry is never empty
            eff_provider, eff_model = cli.resolve_provider(request)
            if eff_provider and not entry.provider:
                self._registry.update_status(
                    entry.task_id, "running", provider=eff_provider, model=eff_model
                )
                entry.provider = eff_provider
                entry.model = eff_model

            process_id = self._record_process_start(entry)
            self._record_message(
                entry,
                role="user",
                content_text=prompt,
                source=message_source,
                process_id=process_id,
                content_json={
                    "task_id": entry.task_id,
                    "chat_id": entry.chat_id,
                    "parent_agent": entry.parent_agent,
                    "resume_session": resume_session or "",
                },
            )

            response = await cli.execute(request)

            elapsed = time.monotonic() - t0
            inflight = self._in_flight.get(entry.task_id)
            has_pending = bool(inflight and inflight.has_pending_question)
            status, error = _classify_task_response(response, timeout, has_pending)
            process_exit_code = _task_process_exit_code(response, status)

            # Accumulate turns (resume adds to previous count)
            total_turns = entry.num_turns + response.num_turns

            self._registry.update_status(
                entry.task_id,
                status,
                session_id=response.session_id or "",
                completed_at=time.time(),
                elapsed_seconds=elapsed,
                error=error,
                result_preview=(response.result or "")[:_RESULT_PREVIEW_LEN],
                num_turns=total_turns,
            )

            result_text = response.result or ""
            session_id = response.session_id or ""

            # Append a concise task-memory summary and artifact list for the parent.
            if status == "done":
                taskmemory = self._registry.taskmemory_path(entry.task_id)
                result_text = _package_task_result(
                    result_text,
                    taskmemory,
                    self._registry.task_folder(entry.task_id),
                )
            elif status == "cancelled":
                taskmemory = self._registry.taskmemory_path(entry.task_id)
                result_text = _append_taskmemory(result_text, taskmemory)

            # Append resume hint so the parent agent knows it can follow up
            if status == "done" and session_id:
                result_text += (
                    f"\n\n---\nTo continue this task's conversation, use:\n"
                    f'python3 tools/task_tools/resume_task.py {entry.task_id} "your follow-up"'
                )

            self._record_message(
                entry,
                role="assistant",
                content_text=result_text or error,
                source="task_result",
                process_id=process_id,
                session_id=session_id,
                content_json={
                    "task_id": entry.task_id,
                    "chat_id": entry.chat_id,
                    "parent_agent": entry.parent_agent,
                    "status": status,
                    "elapsed_seconds": elapsed,
                    "session_id": session_id,
                    "error": error,
                },
            )

            await self._deliver(
                TaskResult(
                    task_id=entry.task_id,
                    chat_id=entry.chat_id,
                    parent_agent=entry.parent_agent,
                    name=entry.name,
                    prompt_preview=entry.prompt_preview,
                    result_text=result_text,
                    status=status,
                    elapsed_seconds=elapsed,
                    provider=entry.provider,
                    model=entry.model,
                    session_id=session_id,
                    error=error,
                    task_folder=str(self._registry.task_folder(entry.task_id)),
                    original_prompt=entry.original_prompt,
                    thread_id=entry.thread_id,
                )
            )

            # Run skill extraction after the parent has already received the
            # final result so extraction cannot hijack completion delivery.
            if status == "done" and self._message_repo:
                self._start_skill_extraction(entry, cli)

        except asyncio.CancelledError:
            elapsed = time.monotonic() - t0
            self._registry.update_status(
                entry.task_id,
                "cancelled",
                completed_at=time.time(),
                elapsed_seconds=elapsed,
            )
            # MED #1: include any partial TASKMEMORY.md the sub-agent wrote
            # before cancellation so the parent sees the progress, not silence.
            taskmemory = self._registry.taskmemory_path(entry.task_id)
            partial_text = _append_taskmemory("", taskmemory)
            with contextlib.suppress(Exception):
                await self._deliver(
                    TaskResult(
                        task_id=entry.task_id,
                        chat_id=entry.chat_id,
                        parent_agent=entry.parent_agent,
                        name=entry.name,
                        prompt_preview=entry.prompt_preview,
                        result_text=partial_text,
                        status="cancelled",
                        elapsed_seconds=elapsed,
                        provider=entry.provider,
                        model=entry.model,
                        original_prompt=entry.original_prompt,
                        thread_id=entry.thread_id,
                    )
                )
            raise

        except Exception:
            logger.exception("Task failed id=%s name='%s'", entry.task_id, entry.name)
            elapsed = time.monotonic() - t0
            error_msg = "Internal error (check logs)"
            process_exit_code = 1
            self._registry.update_status(
                entry.task_id,
                "failed",
                completed_at=time.time(),
                elapsed_seconds=elapsed,
                error=error_msg,
            )
            self._record_message(
                entry,
                role="assistant",
                content_text=error_msg,
                source="task_result",
                process_id=process_id,
                content_json={
                    "task_id": entry.task_id,
                    "chat_id": entry.chat_id,
                    "parent_agent": entry.parent_agent,
                    "status": "failed",
                    "elapsed_seconds": elapsed,
                    "error": error_msg,
                },
            )
            with contextlib.suppress(Exception):
                await self._deliver(
                    TaskResult(
                        task_id=entry.task_id,
                        chat_id=entry.chat_id,
                        parent_agent=entry.parent_agent,
                        name=entry.name,
                        prompt_preview=entry.prompt_preview,
                        result_text="",
                        status="failed",
                        elapsed_seconds=elapsed,
                        provider=entry.provider,
                        model=entry.model,
                        error=error_msg,
                        original_prompt=entry.original_prompt,
                        thread_id=entry.thread_id,
                    )
                )
        finally:
            self._finish_process(process_id, process_exit_code)

    async def _deliver(self, result: TaskResult) -> None:
        """Deliver result to the parent agent's registered callback."""
        handler = self._result_handlers.get(result.parent_agent)
        if handler is None:
            logger.warning(
                "No result handler for parent '%s' task=%s — result lost",
                result.parent_agent,
                result.task_id,
            )
            return
        try:
            await handler(result)
        except Exception:
            logger.exception(
                "Error delivering task result id=%s to '%s'",
                result.task_id,
                result.parent_agent,
            )

    def _start_skill_extraction(self, entry: TaskEntry, cli: CLIService) -> None:
        """Run skill extraction in the background after delivery completes."""
        task = asyncio.create_task(
            self._run_skill_extraction(entry, cli),
            name=f"task-skill-extract:{entry.task_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_skill_extraction(self, entry: TaskEntry, cli: CLIService) -> None:
        """Extract a reusable skill from a completed task without blocking delivery."""
        if not self._message_repo or not self._summary_repo:
            return
        try:
            extractor = SkillExtractor(
                cli_service=cli,
                message_repo=self._message_repo,
                summary_repo=self._summary_repo,
                skills_dir=self._paths.skills_dir,
            )
            await extractor.extract(entry)
        except Exception:
            logger.exception("Skill extraction failed for task %s", entry.task_id)

    def _record_process_start(self, entry: TaskEntry) -> int | None:
        """Persist task process start state when runtime storage is enabled."""
        if self._process_repo is None:
            return None
        try:
            return self._process_repo.create(
                process_label=f"task:{entry.task_id}",
                chat_id=entry.chat_id,
                provider=entry.provider,
                model=entry.model,
                session_storage_key=self._task_session_key(entry.task_id),
            )
        except Exception:
            logger.exception("Failed to record task process start id=%s", entry.task_id)
            return None

    def _finish_process(self, process_id: int | None, exit_code: int | None) -> None:
        """Persist task process completion state when runtime storage is enabled."""
        if self._process_repo is None or process_id is None or exit_code is None:
            return
        with contextlib.suppress(Exception):
            self._process_repo.finish(process_id, exit_code=exit_code)

    def _record_message(
        self,
        entry: TaskEntry,
        *,
        role: str,
        content_text: str,
        source: str,
        process_id: int | None = None,
        session_id: str = "",
        content_json: dict[str, object] | None = None,
    ) -> None:
        """Append a task-local runtime-state message when enabled."""
        if self._message_repo is None:
            return
        payload: dict[str, object] = {
            "task_id": entry.task_id,
            "chat_id": entry.chat_id,
            "parent_agent": entry.parent_agent,
            "session_id": session_id or entry.session_id,
        }
        if content_json:
            payload.update(content_json)
        try:
            self._message_repo.append(
                self._task_session_key(entry.task_id),
                role,
                content_text,
                source=source,
                content_json=payload,
                process_id=process_id,
            )
        except Exception:
            logger.exception("Failed to record task message id=%s source=%s", entry.task_id, source)

    @staticmethod
    def _task_session_key(task_id: str) -> str:
        """Return the synthetic session key for task-scoped runtime messages."""
        return f"task:{task_id}"


_RESULT_PREVIEW_LEN = 200
_TASK_SUMMARY_FRAGMENTS = 4
_TASK_SUMMARY_SNIPPET_LEN = 220
_TASK_ARTIFACT_LIMIT = 12
_IGNORED_TASK_ARTIFACTS = {
    "TASKMEMORY.md",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    "RULES.md",
}
_TASKMEMORY_MAX_LEN = 4000
# Exit codes that map to user-initiated cancel (SIGTERM=15, SIGKILL=9).
# Subprocess module reports these as 128+signal (143/137) or negative signal (-15/-9).
_CANCEL_RETURNCODES = frozenset({143, 137, -15, -9})


def _classify_task_response(
    response: object, timeout: float, has_pending_question: bool
) -> tuple[str, str]:
    """Map a CLIResponse to (status, error_message) for the task registry.

    Exit 143/137 (= 128 + SIGTERM/SIGKILL) means kill_for_task terminated the
    subprocess — surface as ``cancelled``, not ``failed``.
    """
    if getattr(response, "timed_out", False):
        return "failed", f"Timeout after {timeout:.0f}s"
    if getattr(response, "is_error", False):
        if getattr(response, "returncode", None) in _CANCEL_RETURNCODES:
            return "cancelled", ""
        return "failed", getattr(response, "result", None) or "CLI error"
    if has_pending_question:
        return "waiting", ""
    return "done", ""


def _task_process_exit_code(response: object, status: str) -> int:
    """Return the process exit code recorded in runtime state."""
    returncode = getattr(response, "returncode", None)
    if isinstance(returncode, int):
        return returncode
    if getattr(response, "timed_out", False):
        return 124
    if status == "cancelled":
        return 130
    if status == "failed":
        return 1
    return 0


def _package_task_result(result_text: str, taskmemory_path: Path, task_folder: Path) -> str:
    """Append a concise task-memory summary and artifact list to the final result."""
    sections: list[str] = [result_text]
    summary = _summarize_taskmemory(taskmemory_path)
    if summary:
        sections.extend(["---", "TASK MEMORY SUMMARY", summary])

    artifacts = _collect_task_artifacts(task_folder)
    if artifacts:
        artifact_lines = "\n".join(f"- {path}" for path in artifacts)
        sections.extend(["", "---", "ARTIFACTS", artifact_lines])
    return "\n\n".join(section for section in sections if section)


def _summarize_taskmemory(taskmemory_path: Path) -> str:
    """Summarize TASKMEMORY.md into a short, structured fragment digest."""
    try:
        if not taskmemory_path.is_file():
            return ""
        content = taskmemory_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
    except OSError:
        logger.debug("Could not read TASKMEMORY.md at %s", taskmemory_path)
        return ""

    fragments = extract_markdown_fragments(
        content,
        source_path=str(taskmemory_path),
        source_kind="taskmemory",
        scope="taskmemory",
        agent_name="",
    )
    if not fragments:
        return ""

    lines = ["- Task memory fragments:"]
    for fragment in fragments[:_TASK_SUMMARY_FRAGMENTS]:
        snippet = _compact_text(fragment.body, _TASK_SUMMARY_SNIPPET_LEN)
        lines.append(f"- {fragment.title}: {snippet}")
    if len(fragments) > _TASK_SUMMARY_FRAGMENTS:
        lines.append(f"- ... {len(fragments) - _TASK_SUMMARY_FRAGMENTS} more fragment(s)")
    return "\n".join(lines)


def _collect_task_artifacts(task_folder: Path) -> list[str]:
    """Return concise artifact paths from the task folder."""
    if not task_folder.is_dir():
        return []
    paths: list[str] = []
    for path in sorted(task_folder.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _IGNORED_TASK_ARTIFACTS:
            continue
        rel = path.relative_to(task_folder).as_posix()
        paths.append(rel)
        if len(paths) >= _TASK_ARTIFACT_LIMIT:
            break
    return paths


def _compact_text(text: str, limit: int) -> str:
    """Collapse whitespace and trim a summary line to a fixed length."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _append_taskmemory(result_text: str, taskmemory_path: Path) -> str:
    """Append TASKMEMORY.md content to the result so the parent gets full context."""
    try:
        if not taskmemory_path.is_file():
            return result_text
        content = taskmemory_path.read_text(encoding="utf-8").strip()
        if not content:
            return result_text
    except OSError:
        logger.debug("Could not read TASKMEMORY.md at %s", taskmemory_path)
        return result_text

    if len(content) > _TASKMEMORY_MAX_LEN:
        original_len = len(content)
        logger.warning(
            "TASKMEMORY truncated at %s: %d chars -> %d chars "
            "(parent agent sees 'full content at' hint)",
            taskmemory_path,
            original_len,
            _TASKMEMORY_MAX_LEN,
        )
        content = (
            content[:_TASKMEMORY_MAX_LEN]
            + f"\n[... truncated -- original was {original_len} chars. "
            + f"Full content at: {taskmemory_path}]"
        )

    return f"{result_text}\n\n---\nCONTENT FROM TASKMEMORY.MD ({taskmemory_path}):\n\n{content}"
