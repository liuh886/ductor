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

You MUST include valid YAML frontmatter at the very top of the Markdown file, enclosed by `---`.
The YAML frontmatter MUST contain the following required keys:
- `name`: A short, descriptive name for the skill (e.g., 'sqlite-wal-debugger').
- `description`: A brief summary of what the skill does.
- `phase_trigger`: The phase when this skill should be used (e.g., RESEARCH, DESIGN, EXECUTION, DEBUGGING).

Example:
---
name: sqlite-wal-debugger
description: Guide on how to debug SQLite Write-Ahead Logging issues.
phase_trigger: DEBUGGING
---

Focus on:
1. Successful steps taken.
2. Tools used and why.
3. Key findings or insights.
4. Specific commands or code snippets that were effective.

The output should be a single Markdown file with:
- The required YAML frontmatter at the very top.
- A clear, descriptive title (e.g., '# How to Debug SQLite WAL Issues').
- A 'Context' or 'Objective' section.
- A 'Procedural Steps' section (numbered).
- A 'Key Findings' section.
- A 'Tools & Commands' section.

Task Name: {task_name}
Original Prompt: {original_prompt}

Execution History (Compressed):
{history}

Return ONLY the Markdown content for the skill (including the frontmatter). Do NOT wrap the output in ```markdown blocks.
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
            trigger_messages=8,
            protected_tail=2,
            max_summary_items=6,
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

            import re
            skill_content = response.result.strip()
            # Basic cleanup of LLM-wrapped markdown blocks
            skill_content = re.sub(r"^```[mM]arkdown\s*\n", "", skill_content)
            skill_content = re.sub(r"^```\s*\n", "", skill_content)
            skill_content = re.sub(r"\n```\s*$", "", skill_content)
            skill_content = skill_content.strip()

            # Generate a safe skill directory name.
            safe_name = "".join(c if c.isalnum() else "_" for c in (entry.name or entry.task_id))
            skill_dir = self._skills_dir / f"skill_{safe_name[:40]}_{entry.task_id}"
            skill_path = skill_dir / "SKILL.md"

            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(skill_content, encoding="utf-8")

            logger.info("Skill extracted to %s", skill_path)

            # Update Capability Registry
            try:
                name, desc, phase = safe_name, "Extracted skill", "EXECUTION"

                # Extract frontmatter
                frontmatter_match = re.search(r"^---\s*\n(.*?)\n---", skill_content, flags=re.DOTALL)
                if frontmatter_match:
                    frontmatter = frontmatter_match.group(1)

                    name_match = re.search(r"^name:\s*(.*?)$", frontmatter, flags=re.MULTILINE)
                    if name_match:
                        name = name_match.group(1).strip(" '\"")

                    desc_match = re.search(r"^description:\s*(.*?)$", frontmatter, flags=re.MULTILINE)
                    if desc_match:
                        desc = desc_match.group(1).strip(" '\"")

                    phase_match = re.search(r"^phase_trigger:\s*(.*?)$", frontmatter, flags=re.MULTILINE)
                    if phase_match:
                        phase = phase_match.group(1).strip(" '\"")

                registry_path = self._skills_dir.parent.parent / "memory_system" / "CAPABILITY_REGISTRY.md"
                if registry_path.exists():
                    registry_content = registry_path.read_text(encoding="utf-8")

                    # Ensure we have an Extracted Skills section
                    if "## Extracted Skills" not in registry_content:
                        with registry_path.open("a", encoding="utf-8") as f:
                            f.write("\n## Extracted Skills\n\n")

                    entry_line = f"- **{name}** ({phase}): {desc} [Dir: {skill_dir.name}]\n"
                    with registry_path.open("a", encoding="utf-8") as f:
                        f.write(entry_line)

                    logger.info("Added skill %s to Capability Registry.", name)
            except Exception as e:
                logger.error("Failed to update Capability Registry: %s", e)

            return skill_dir

        except Exception:
            logger.exception("Failed to extract skill for %s", entry.task_id)
            return None
