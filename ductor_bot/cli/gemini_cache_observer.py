"""Background observer for periodic Gemini model cache refresh."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

from ductor_bot.cli.gemini_cache import GeminiModelCache

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_S: int = 3600


class GeminiCacheObserver:
    """Refreshes Gemini model cache periodically.

    Loads initial cache at startup and refreshes every 60 minutes.
    """

    def __init__(
        self,
        cache_path: Path,
        *,
        on_refresh: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        """Initialize observer with cache file path.

        Args:
            cache_path: Path to JSON cache file.
            on_refresh: Optional callback invoked with the model list after
                        each successful cache load/refresh.
        """
        self._cache_path = cache_path
        self._on_refresh = on_refresh
        self._cache: GeminiModelCache | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Load initial cache and start refresh loop."""
        logger.info("GeminiCacheObserver starting, cache_path=%s", self._cache_path)
        self._cache = await GeminiModelCache.load_or_refresh(self._cache_path)
        self._notify(self._cache)
        logger.info(
            "Gemini cache loaded: %d models, last_updated=%s",
            len(self._cache.models),
            self._cache.last_updated,
        )
        self._running = True
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Stop refresh loop."""
        logger.info("GeminiCacheObserver stopping")
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def get_cache(self) -> GeminiModelCache | None:
        """Return current cache (may be None if never loaded)."""
        return self._cache

    def _notify(self, cache: GeminiModelCache) -> None:
        """Invoke on_refresh callback if set."""
        if self._on_refresh and cache.models:
            self._on_refresh(cache.models)

    async def _refresh_loop(self) -> None:
        """Refresh cache every 60 minutes."""
        try:
            while self._running:
                await asyncio.sleep(REFRESH_INTERVAL_S)
                if not self._running:
                    break  # type: ignore[unreachable]
                try:
                    logger.info("GeminiCacheObserver: refreshing cache")
                    self._cache = await GeminiModelCache.load_or_refresh(self._cache_path)
                    self._notify(self._cache)
                    logger.info(
                        "Gemini cache refreshed: %d models",
                        len(self._cache.models),
                    )
                except Exception:
                    logger.exception("Gemini cache refresh failed, will retry in 60 minutes")
        except asyncio.CancelledError:
            logger.debug("GeminiCacheObserver refresh loop cancelled")
            raise
