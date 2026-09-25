"""Centralized logging setup: console (colored) + rotating file.

Call ``setup_logging()`` once at startup.  All modules use ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import atexit
import logging
import queue
import sys
import time
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 3

NETWORK_ERROR_LOG_INTERVAL_SECONDS = 300.0
"""Repeated aiogram polling network errors are collapsed to one line per window."""

_NETWORK_ERROR_MARKERS = ("Failed to fetch updates", "TelegramNetworkError")

CONSOLE_FMT = "%(asctime)s %(levelname)s %(name)s: %(ctx)s%(message)s"
FILE_FMT = "%(asctime)s [%(levelname)s] %(name)s:%(filename)s:%(lineno)d: %(ctx)s%(message)s"
DATE_FMT = "%H:%M:%S"
FILE_DATE_FMT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger(__name__)

_ANSI = {
    "DEBUG": "\x1b[36m",
    "INFO": "\x1b[32m",
    "WARNING": "\x1b[33m",
    "ERROR": "\x1b[31m",
    "CRITICAL": "\x1b[35m",
}
_RESET = "\x1b[0m"


class _LogState:
    """Mutable container for module-level logging state."""

    listener: QueueListener | None = None
    atexit_registered: bool = False


_state = _LogState()


def _stop_queue_listener() -> None:
    if _state.listener is not None:
        _state.listener.stop()
        _state.listener = None


class _NetworkErrorRateLimitFilter(logging.Filter):
    """Collapse repeated aiogram polling network errors into one line per window.

    Telegram outages (proxy restarts, sleep/resume) make every bot log a
    ``Failed to fetch updates`` line on each poll retry, drowning real errors.
    The first line of a window is kept (with the count of identical errors
    suppressed afterwards); the rest is dropped.
    """

    def __init__(self, interval_seconds: float = NETWORK_ERROR_LOG_INTERVAL_SECONDS) -> None:
        super().__init__()
        self._interval = interval_seconds
        self._last_emitted = 0.0
        self._suppressed = 0

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if not all(marker in message for marker in _NETWORK_ERROR_MARKERS):
            return True
        now = time.monotonic()
        if self._last_emitted and now - self._last_emitted < self._interval:
            self._suppressed += 1
            return False
        if self._suppressed:
            record.msg = f"{message} (+{self._suppressed} identical errors suppressed)"
            record.args = None
            self._suppressed = 0
        self._last_emitted = now
        return True


class _ColorFormatter(logging.Formatter):
    """ANSI-colored level names for terminal output."""

    def __init__(self, fmt: str, datefmt: str | None = None, use_color: bool = True) -> None:
        super().__init__(fmt, datefmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        original = record.levelname
        if self._use_color:
            color = _ANSI.get(original, "")
            record.levelname = f"{color}{original:<8}{_RESET}"
        else:
            record.levelname = f"{original:<8}"
        try:
            return super().format(record)
        finally:
            record.levelname = original


def setup_logging(
    level: int = logging.INFO,
    verbose: bool = False,
    log_dir: Path | None = None,
) -> None:
    """Configure root logger with console + optional rotating file handler.

    Args:
        level: Minimum log level. DEBUG if verbose, else INFO.
        verbose: If True, sets DEBUG level and enables debug output.
        log_dir: Directory for log files. If None, file logging is skipped.
    """
    if verbose:
        level = logging.DEBUG

    _stop_queue_listener()

    from ductor_bot.log_context import ContextFilter

    ctx_filter = ContextFilter()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # pythonw.exe (Windows service) sets sys.stderr to None
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.addFilter(ctx_filter)
        use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        console_handler.setFormatter(
            _ColorFormatter(CONSOLE_FMT, datefmt=DATE_FMT, use_color=use_color)
        )
        root.addHandler(console_handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "agent.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(FILE_FMT, datefmt=FILE_DATE_FMT))

        log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
        queue_handler = QueueHandler(log_queue)
        queue_handler.setLevel(logging.DEBUG)
        queue_handler.addFilter(ctx_filter)
        root.addHandler(queue_handler)

        listener = QueueListener(log_queue, file_handler, respect_handler_level=True)
        listener.start()
        _state.listener = listener
        if not _state.atexit_registered:
            atexit.register(_stop_queue_listener)
            _state.atexit_registered = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)

    aiogram_dispatcher = logging.getLogger("aiogram.dispatcher")
    if not any(isinstance(f, _NetworkErrorRateLimitFilter) for f in aiogram_dispatcher.filters):
        aiogram_dispatcher.addFilter(_NetworkErrorRateLimitFilter())

    logger.info("Logging initialized (level=%s)", logging.getLevelName(level))
