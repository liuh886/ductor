"""Governed Context Builder: Assembles agent context based on dynamic budgeting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ductor_bot.cli.types import AgentRequest
    from ductor_bot.session import SessionData

logger = logging.getLogger(__name__)

@dataclass
class ContextBudget:
    """Token budget allocation for different context layers."""
    total_limit: int = 128000  # Default for Sonnet/Gemini
    index_pct: float = 0.20    # MAINMEMORY / SOPs
    state_pct: float = 0.25    # Task States from DB
    history_pct: float = 0.45  # Recent conversation
    rag_pct: float = 0.10      # Vector retrieval

    @property
    def index_tokens(self) -> int:
        return int(self.total_limit * self.index_pct)

    @property
    def state_tokens(self) -> int:
        return int(self.total_limit * self.state_pct)

    @property
    def history_tokens(self) -> int:
        return int(self.total_limit * self.history_pct)

    @property
    def rag_tokens(self) -> int:
        return int(self.total_limit * self.rag_pct)


class ContextBuilder:
    """Assembles AgentRequest by applying governance rules and token budgeting."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    def build_request(  # noqa: PLR0913
        self,
        *,
        user_prompt: str,
        soul: str | None = None,
        main_memory: str | None = None,
        task_state: str | None = None,
        recent_history: str | None = None,
        rag_fragments: list[str] | None = None,
        session: SessionData,
        model: str,
        provider: str,
    ) -> AgentRequest:
        """Constructs an AgentRequest while respecting the budget for each component.

        Note: Current implementation uses character-count estimation (1 token ~ 4 chars).
        Future versions will plug in real tiktoken/provider-specific counters.
        """
        from ductor_bot.cli.types import AgentRequest

        # 1. Assemble System Prompt (Identity-adjacent operational notes + memory)
        system_parts = []
        if soul:
            limited_soul = self._truncate_to_budget(soul, max(512, self.budget.index_tokens // 4))
            system_parts.append(f"# Operational Notes\n{limited_soul}")

        if main_memory:
            limited_mem = self._truncate_to_budget(main_memory, self.budget.index_tokens)
            system_parts.append(f"# Memory Context\n{limited_mem}")

        # 2. Assemble Task State (situation/state context)
        state_parts = []
        if task_state:
            limited_state = self._truncate_to_budget(task_state, self.budget.state_tokens)
            state_parts.append(f"[SITUATION_CONTEXT]\n{limited_state}")

        # 3. Assemble RAG Context (L5 logic)
        rag_text = ""
        if rag_fragments:
            combined_rag = "\n\n".join(rag_fragments)
            rag_text = self._truncate_to_budget(combined_rag, self.budget.rag_tokens)

        # 4. Construct the prompt
        # If the session is new, we send the full history context.
        # If resuming, we rely on the provider's session (but this Builder allows manual injection).
        final_prompt = user_prompt
        if state_parts:
            final_prompt = "\n\n".join(state_parts) + "\n\n" + final_prompt

        if rag_text:
            final_prompt = f"[RELEVANT_CONTEXT]\n{rag_text}\n\n" + final_prompt

        # 5. Assemble final request specification
        return AgentRequest(
            prompt=final_prompt,
            system_prompt="\n\n".join(system_parts) if system_parts else None,
            model_override=model,
            provider_override=provider,
            transport=session.transport,
            chat_id=session.chat_id,
            topic_id=session.topic_id,
            resume_session=session.session_id if session.message_count > 0 else None,
            process_label="main",
        )

    def _truncate_to_budget(self, text: str, token_limit: int) -> str:
        """Simple truncation based on character estimation."""
        char_limit = token_limit * 4
        if len(text) <= char_limit:
            return text
        logger.warning("ContextBuilder: Truncating text over budget (%d tokens)", token_limit)
        return text[:char_limit] + "\n... [TRUNCATED DUE TO BUDGET] ..."
