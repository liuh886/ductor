"""Persistent cache for Gemini models with periodic refresh."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ductor_bot.cli.gemini_utils import discover_gemini_models

logger = logging.getLogger(__name__)

_CACHE_MAX_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class GeminiModelCache:
    """Immutable cache of Gemini model IDs with refresh logic."""

    last_updated: str  # ISO 8601 timestamp
    models: tuple[str, ...]

    def validate_model(self, model_id: str) -> bool:
        """Check if model exists in cache."""
        return model_id in self.models

    @classmethod
    async def load_or_refresh(
        cls,
        cache_path: Path,
        *,
        force_refresh: bool = False,
    ) -> GeminiModelCache:
        """Load from disk, refresh if stale (>24h) or missing.

        Args:
            cache_path: Path to JSON cache file
            force_refresh: If True, ignore on-disk cache and rediscover models

        Returns:
            GeminiModelCache (possibly refreshed)
        """
        if force_refresh:
            logger.info("Gemini cache refresh forced")
            return await cls._refresh_and_save(cache_path)

        exists = await asyncio.to_thread(cache_path.exists)
        if exists:
            try:
                content = await asyncio.to_thread(cache_path.read_text)
                data = json.loads(content)
                cache = cls.from_json(data)

                last_updated = datetime.fromisoformat(cache.last_updated)
                age = datetime.now(UTC) - last_updated

                if age < _CACHE_MAX_AGE:
                    if cache.models:
                        logger.debug("Gemini cache is fresh, using cached models")
                        return cache

                    logger.info("Gemini cache is fresh but empty, forcing refresh")
                else:
                    logger.info("Gemini cache is stale (age: %s), refreshing", age)
            except Exception:
                logger.warning("Failed to load Gemini cache, will refresh", exc_info=True)

        return await cls._refresh_and_save(cache_path)

    @classmethod
    async def _refresh_and_save(cls, cache_path: Path) -> GeminiModelCache:
        """Discover models and save to disk."""
        try:
            discovered = await asyncio.to_thread(discover_gemini_models)
            models = tuple(sorted(discovered))
            logger.info("Discovered %d Gemini models", len(models))
        except Exception:
            logger.exception("Failed to discover Gemini models, using empty cache")
            models = ()

        cache = cls(
            last_updated=datetime.now(UTC).isoformat(),
            models=models,
        )

        try:
            await asyncio.to_thread(cache_path.parent.mkdir, parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            content = json.dumps(cache.to_json(), indent=2)
            await asyncio.to_thread(temp_path.write_text, content)
            await asyncio.to_thread(temp_path.replace, cache_path)
            logger.debug("Saved Gemini cache to %s", cache_path)
        except Exception:
            logger.exception("Failed to save Gemini cache to disk")

        return cache

    def to_json(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "last_updated": self.last_updated,
            "models": list(self.models),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> GeminiModelCache:
        """Deserialize from JSON."""
        return cls(
            last_updated=data["last_updated"],
            models=tuple(data["models"]),
        )
