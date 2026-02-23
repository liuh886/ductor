"""Tests for Gemini model cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from ductor_bot.cli.gemini_cache import GeminiModelCache


@pytest.fixture
def sample_models() -> tuple[str, ...]:
    """Sample model tuple for testing."""
    return ("gemini-2.5-flash", "gemini-2.5-pro")


@pytest.fixture
def fresh_cache(sample_models: tuple[str, ...]) -> GeminiModelCache:
    """Fresh cache (< 24h old)."""
    return GeminiModelCache(
        last_updated=datetime.now(UTC).isoformat(),
        models=sample_models,
    )


@pytest.fixture
def stale_cache(sample_models: tuple[str, ...]) -> GeminiModelCache:
    """Stale cache (> 24h old)."""
    old_time = datetime.now(UTC) - timedelta(hours=25)
    return GeminiModelCache(
        last_updated=old_time.isoformat(),
        models=sample_models,
    )


async def test_load_from_disk(tmp_path: Path) -> None:
    """Should load cache from disk if present and fresh."""
    cache_path = tmp_path / "gemini_models.json"
    now = datetime.now(UTC).isoformat()
    cache_path.write_text(
        f'{{"last_updated": "{now}", "models": ["gemini-2.5-flash", "gemini-2.5-pro"]}}'
    )

    with patch("ductor_bot.cli.gemini_cache.discover_gemini_models") as mock_discover:
        result = await GeminiModelCache.load_or_refresh(cache_path)

        assert len(result.models) == 2
        assert result.models[0] == "gemini-2.5-flash"
        mock_discover.assert_not_called()


async def test_refresh_on_stale(tmp_path: Path, sample_models: tuple[str, ...]) -> None:
    """Should refresh cache if stale (>24h)."""
    cache_path = tmp_path / "gemini_models.json"
    old_time = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    cache_path.write_text(f'{{"last_updated": "{old_time}", "models": []}}')

    with patch(
        "ductor_bot.cli.gemini_cache.discover_gemini_models",
        return_value=frozenset(sample_models),
    ) as mock_discover:
        result = await GeminiModelCache.load_or_refresh(cache_path)

        mock_discover.assert_called_once()
        assert len(result.models) == 2
        assert cache_path.exists()


async def test_skip_refresh_if_recent(tmp_path: Path) -> None:
    """Should skip refresh if cache is recent (<24h)."""
    cache_path = tmp_path / "gemini_models.json"
    recent_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    cache_path.write_text(f'{{"last_updated": "{recent_time}", "models": ["gemini-2.5-flash"]}}')

    with patch("ductor_bot.cli.gemini_cache.discover_gemini_models") as mock_discover:
        result = await GeminiModelCache.load_or_refresh(cache_path)

        mock_discover.assert_not_called()
        assert len(result.models) == 1


async def test_refresh_if_recent_but_empty(
    tmp_path: Path,
    sample_models: tuple[str, ...],
) -> None:
    """Should refresh if cache is recent but contains zero models."""
    cache_path = tmp_path / "gemini_models.json"
    recent_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    cache_path.write_text(f'{{"last_updated": "{recent_time}", "models": []}}')

    with patch(
        "ductor_bot.cli.gemini_cache.discover_gemini_models",
        return_value=frozenset(sample_models),
    ) as mock_discover:
        result = await GeminiModelCache.load_or_refresh(cache_path)

        mock_discover.assert_called_once()
        assert len(result.models) == 2


async def test_force_refresh_ignores_fresh_cache(
    tmp_path: Path,
    sample_models: tuple[str, ...],
) -> None:
    """Should refresh when force_refresh=True even if cache is fresh."""
    cache_path = tmp_path / "gemini_models.json"
    recent_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    cache_path.write_text(f'{{"last_updated": "{recent_time}", "models": ["old-model"]}}')

    with patch(
        "ductor_bot.cli.gemini_cache.discover_gemini_models",
        return_value=frozenset(sample_models),
    ) as mock_discover:
        result = await GeminiModelCache.load_or_refresh(cache_path, force_refresh=True)

        mock_discover.assert_called_once()
        assert len(result.models) == 2
        assert "gemini-2.5-flash" in result.models


def test_validate_model_exists(fresh_cache: GeminiModelCache) -> None:
    """Should return True for existing model."""
    assert fresh_cache.validate_model("gemini-2.5-flash") is True
    assert fresh_cache.validate_model("gemini-2.5-pro") is True


def test_validate_model_missing(fresh_cache: GeminiModelCache) -> None:
    """Should return False for nonexistent model."""
    assert fresh_cache.validate_model("nonexistent") is False


async def test_cache_empty_on_discovery_failure(tmp_path: Path) -> None:
    """Should create empty cache if discovery fails."""
    cache_path = tmp_path / "gemini_models.json"

    with patch(
        "ductor_bot.cli.gemini_cache.discover_gemini_models",
        side_effect=Exception("Discovery failed"),
    ):
        result = await GeminiModelCache.load_or_refresh(cache_path)

        assert len(result.models) == 0


def test_serialize_deserialize(fresh_cache: GeminiModelCache) -> None:
    """Should roundtrip serialize and deserialize."""
    json_data = fresh_cache.to_json()

    assert "last_updated" in json_data
    assert "models" in json_data
    assert len(json_data["models"]) == 2

    restored = GeminiModelCache.from_json(json_data)

    assert restored.last_updated == fresh_cache.last_updated
    assert len(restored.models) == len(fresh_cache.models)
    assert restored.models[0] == fresh_cache.models[0]
