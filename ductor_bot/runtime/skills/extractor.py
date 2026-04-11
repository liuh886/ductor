from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ductor_bot.cli.types import AgentRequest
from ductor_bot.runtime.compression.summary_selector import SummarySelector

if TYPE_CHECKING:
    from pathlib import Path

    from ductor_bot.cli.service import CLIService
    from ductor_bot.runtime.state import MessageRepository, SessionSummaryRepository
    from ductor_bot.tasks.models import TaskEntry

logger = logging.getLogger(__name__)

SKILL_EXTRACTION_PROMPT = """
Analyze the following task execution history and extract a reusable 'Skill' in Markdown format.
A Skill should be a procedural 'How-To' guide that can be followed by another agent to achieve a similar result.

Focus on:
1. Successful steps taken.
2. Tools used and why.
3. Key findings or insights.
4. Specific commands or code snippets that were effective.

The output should be a single Markdown file with:
- A clear, descriptive title (e.g., 'How to Debug SQLite WAL Issues').
- A 'Context' or 'Objective' section.
- A 'Procedural Steps' section (numbered).
- A 'Key Findings' section.
- A 'Tools & Commands' section.

Task Name: {task_name}
Original Prompt: {original_prompt}

Execution History (Compressed):
{history}

Return ONLY the Markdown content for the skill.
"""


class SkillExtractor:
    """Extracts reusable skills from successful task executions."""

    def __init__(
        self,
        cli_service: CLIService,
        message_repo: MessageRepository,
        summary_repo: SessionSummaryRepository,
        skills_dir: Path,
    ) -> None:
        self._cli_service = cli_service
        self._message_repo = message_repo
        self._summary_repo = summary_repo
        self._skills_dir = skills_dir
        # Use more aggressive triggers for skill extraction (max 15 items in history)
        self._selector = SummarySelector(
            message_repo,
            summary_repo,
            trigger_messages=12,
            protected_tail=6,
            max_summary_items=10,
        )

    async def extract(self, entry: TaskEntry) -> Path | None:
        """Extract a skill from a completed task and save it to the skills directory."""
        try:
            session_key = f"task:{entry.task_id}"
            
            # Use the selector to get a compact version of the history
            selection = self._selector.select(session_key)
            
            history_text = ""
            if selection.summary_text:
                history_text += f"Summary of earlier steps:\n{selection.summary_text}\n\nRecent Details:\n"
            
            for msg in selection.tail_messages:
                role = str(msg.get("role", "unknown"))
                content = str(msg.get("content_text", ""))
                history_text += f"--- {role.upper()} ---\n{content[:2000]}\n" # Cap individual msg length

            if not history_text.strip():
                logger.warning("No usable history for task %s, skipping extraction", entry.task_id)
                return None

            prompt = SKILL_EXTRACTION_PROMPT.format(
                task_name=entry.name or entry.task_id,
                original_prompt=entry.original_prompt or entry.prompt_preview,
                history=history_text,
            )

            request = AgentRequest(
                prompt=prompt,
                model_override=entry.model or None,
                provider_override=entry.provider or None,
                process_label=f"skill_extraction:{entry.task_id}",
                chat_id=entry.chat_id,
                timeout_seconds=300,
            )

            response = await self._cli_service.execute(request)
            if response.is_error or not response.result:
                logger.error("Skill extraction failed for %s", entry.task_id)
                return None

            skill_content = response.result.strip()
            # Basic cleanup of LLM-wrapped markdown blocks
            for prefix in ("```markdown", "```"):
                if skill_content.lower().startswith(prefix):
                    skill_content = skill_content[len(prefix) :].strip()
            if skill_content.endswith("```"):
                skill_content = skill_content[:-3].strip()

            # Generate a safe skill directory name.
            safe_name = "".join(c if c.isalnum() else "_" for c in (entry.name or entry.task_id))
            skill_dir = self._skills_dir / f"skill_{safe_name[:40]}_{entry.task_id}"
            skill_path = skill_dir / "SKILL.md"

            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(skill_content, encoding="utf-8")

            logger.info("Skill extracted to %s", skill_path)
            return skill_dir

        except Exception:
            logger.exception("Failed to extract skill for %s", entry.task_id)
            return None
