"""Tests for model-cache fallback warning rate limiting."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.cli import model_cache as model_cache_module
from ductor_bot.cli.grok_cache import GrokModelCache

if TYPE_CHECKING:
    import pytest


async def _load_forced(cache_path: Path) -> GrokModelCache:
    return await GrokModelCache.load_or_refresh(cache_path, force_refresh=True)


async def _discover_nothing() -> tuple[str, ...]:
    return ()


async def _discover_real() -> tuple[str, ...]:
    return ("grok-real-model",)


async def test_fallback_warning_logged_once_per_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import ductor_bot.cli.grok_cache as grok_cache_module

    model_cache_module._fallback_warned.discard("Grok")
    monkeypatch.setattr(grok_cache_module, "discover_grok_models", _discover_nothing)
    cache_path = tmp_path / "grok_models.json"

    with caplog.at_level(logging.WARNING, logger="ductor_bot.cli.model_cache"):
        first = await _load_forced(cache_path)
        second = await _load_forced(cache_path)

    assert first.models
    assert second.models == first.models
    warnings = [r for r in caplog.records if "hardcoded Grok fallback" in r.getMessage()]
    assert len(warnings) == 1


async def test_fallback_warning_returns_after_successful_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import ductor_bot.cli.grok_cache as grok_cache_module

    model_cache_module._fallback_warned.discard("Grok")
    cache_path = tmp_path / "grok_models.json"
    monkeypatch.setattr(grok_cache_module, "discover_grok_models", _discover_nothing)

    with caplog.at_level(logging.WARNING, logger="ductor_bot.cli.model_cache"):
        await _load_forced(cache_path)
        monkeypatch.setattr(grok_cache_module, "discover_grok_models", _discover_real)
        discovered = await _load_forced(cache_path)
        assert discovered.models == ("grok-real-model",)
        assert cache_path.is_file()

        cache_path.unlink()
        monkeypatch.setattr(grok_cache_module, "discover_grok_models", _discover_nothing)
        await _load_forced(cache_path)

    warnings = [r for r in caplog.records if "hardcoded Grok fallback" in r.getMessage()]
    assert len(warnings) == 2
