"""Orchestrator package exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ductor_bot.orchestrator.core import Orchestrator
    from ductor_bot.orchestrator.registry import OrchestratorResult

__all__ = ["Orchestrator", "OrchestratorResult"]


def __getattr__(name: str) -> Any:
    """Resolve package exports lazily to avoid import cycles."""
    if name == "Orchestrator":
        from ductor_bot.orchestrator.core import Orchestrator

        return Orchestrator
    if name == "OrchestratorResult":
        from ductor_bot.orchestrator.registry import OrchestratorResult

        return OrchestratorResult
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
