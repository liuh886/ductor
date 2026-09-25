"""Fold over-long streamed replies into a single document attachment.

Telegram messages are capped at 4096 chars; a very long answer would otherwise
flood the chat with dozens of messages. When a reply exceeds the configured
``streaming.max_messages`` limit, the streamed messages keep the beginning of
the answer and the complete text is attached as a Markdown file.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile

from ductor_bot.i18n import t

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

logger = logging.getLogger(__name__)

_REPLY_FILE_PREFIX = "ductor_reply_"
_REPLY_FILE_MAX_AGE_SECONDS = 24 * 3600


def write_reply_file(text: str) -> Path:
    """Write *text* to a fresh temp Markdown file and sweep stale reply files."""
    directory = Path(tempfile.gettempdir())
    cutoff = time.time() - _REPLY_FILE_MAX_AGE_SECONDS
    for stale in directory.glob(f"{_REPLY_FILE_PREFIX}*.md"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue
    fd, name = tempfile.mkstemp(prefix=_REPLY_FILE_PREFIX, suffix=".md", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return Path(name)


async def send_reply_document(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    thread_id: int | None = None,
) -> Message | None:
    """Send the complete reply as a Markdown document; return its message."""
    path = write_reply_file(text)
    try:
        return await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(path),
            caption=t("stream.folded_reply"),
            message_thread_id=thread_id,
        )
    except TelegramRetryAfter as exc:
        logger.warning("Folded reply hit rate limit: %s", exc)
    except (TelegramBadRequest, TelegramNetworkError) as exc:
        logger.warning("Failed to send folded reply document: %s", exc)
    return None
