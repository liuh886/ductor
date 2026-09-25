"""Tests for centralized logging setup."""

from __future__ import annotations

import logging
from pathlib import Path


class TestSetupLogging:
    """Test logging configuration."""

    def test_sets_root_level(self) -> None:
        from ductor_bot.logging_config import setup_logging

        setup_logging(level=logging.WARNING, log_dir=None)
        assert logging.getLogger().level == logging.WARNING

    def test_verbose_sets_debug(self) -> None:
        from ductor_bot.logging_config import setup_logging

        setup_logging(verbose=True, log_dir=None)
        assert logging.getLogger().level == logging.DEBUG

    def test_console_handler_added(self) -> None:
        from ductor_bot.logging_config import setup_logging

        setup_logging(log_dir=None)
        root = logging.getLogger()
        handler_types = [type(h).__name__ for h in root.handlers]
        assert "StreamHandler" in handler_types

    def test_file_handler_when_log_dir(self, tmp_path: Path) -> None:
        from ductor_bot.logging_config import setup_logging

        log_dir = tmp_path / "logs"
        setup_logging(log_dir=log_dir)
        assert log_dir.exists()
        # QueueHandler should be present (for async file writing)
        root = logging.getLogger()
        handler_types = [type(h).__name__ for h in root.handlers]
        assert "QueueHandler" in handler_types

    def test_noisy_loggers_quieted(self) -> None:
        from ductor_bot.logging_config import setup_logging

        setup_logging(log_dir=None)
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING

    def test_repeated_calls_no_duplicate_handlers(self) -> None:
        from ductor_bot.logging_config import setup_logging

        setup_logging(log_dir=None)
        setup_logging(log_dir=None)
        root = logging.getLogger()
        # Should not accumulate handlers
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stream_handlers) <= 2  # Console + possibly QueueHandler


class TestNetworkErrorRateLimit:
    """Repeated Telegram polling network errors must be rate-limited in logs."""

    @staticmethod
    def _record(message: str) -> logging.LogRecord:
        return logging.LogRecord(
            "aiogram.dispatcher", logging.ERROR, __file__, 0, message, (), None
        )

    _MESSAGE = "Failed to fetch updates - TelegramNetworkError: boom"

    def test_repeats_suppressed_and_summarized(self) -> None:
        from ductor_bot.logging_config import _NetworkErrorRateLimitFilter

        rate_filter = _NetworkErrorRateLimitFilter(interval_seconds=300)
        first = self._record(self._MESSAGE)
        second = self._record(self._MESSAGE)
        assert rate_filter.filter(first) is True
        assert rate_filter.filter(second) is False

        rate_filter._last_emitted -= 301
        third = self._record(self._MESSAGE)
        assert rate_filter.filter(third) is True
        assert "+1 identical errors suppressed" in third.getMessage()

    def test_unrelated_records_pass_through(self) -> None:
        from ductor_bot.logging_config import _NetworkErrorRateLimitFilter

        rate_filter = _NetworkErrorRateLimitFilter(interval_seconds=300)
        assert rate_filter.filter(self._record(self._MESSAGE)) is True
        other = self._record("Ordered shutdown complete")
        assert rate_filter.filter(other) is True
        assert "suppressed" not in other.getMessage()

    def test_setup_logging_attaches_filter_once(self) -> None:
        from ductor_bot import logging_config

        logging_config.setup_logging(log_dir=None)
        logging_config.setup_logging(log_dir=None)
        dispatcher = logging.getLogger("aiogram.dispatcher")
        attached = [
            f
            for f in dispatcher.filters
            if isinstance(f, logging_config._NetworkErrorRateLimitFilter)
        ]
        assert len(attached) == 1


class TestColorFormatter:
    """Test ANSI color formatting."""

    def test_color_applied_when_enabled(self) -> None:
        from ductor_bot.logging_config import _ColorFormatter

        fmt = _ColorFormatter("%(levelname)s: %(message)s", use_color=True)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        result = fmt.format(record)
        assert "\x1b[" in result  # ANSI escape present

    def test_no_color_when_disabled(self) -> None:
        from ductor_bot.logging_config import _ColorFormatter

        fmt = _ColorFormatter("%(levelname)s: %(message)s", use_color=False)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        result = fmt.format(record)
        assert "\x1b[" not in result
