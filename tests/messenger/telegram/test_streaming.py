"""Tests for StreamEditor (append-mode: each chunk sent as new formatted message)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from aiogram.enums import ParseMode
from aiogram.types import Message

from ductor_bot.messenger.telegram.streaming import StreamEditor


class TestStreamEditor:
    """Test append-mode streaming: chunks sent as new messages, no edits."""

    async def test_has_content_initially_false(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        editor = StreamEditor(bot, chat_id=1)
        assert editor.has_content is False

    async def test_append_text_sends_new_message(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("Hello **world**")
        assert editor.has_content is True
        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs["parse_mode"] == ParseMode.HTML
        assert "<b>world</b>" in call_kwargs["text"]

    async def test_reply_to_first_message_only(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        reply_msg = MagicMock(spec=Message)
        sent_msg = MagicMock(spec=Message)
        reply_msg.answer = AsyncMock(return_value=sent_msg)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1, reply_to=reply_msg)
        await editor.append_text("First chunk")
        reply_msg.answer.assert_called_once()

        # Second chunk should NOT reply, but send as new message
        await editor.append_text("Second chunk")
        assert bot.send_message.call_count == 1
        assert reply_msg.answer.call_count == 1

    async def test_multiple_chunks_each_new_message(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("Chunk 1")
        await editor.append_text("Chunk 2")
        await editor.append_text("Chunk 3")
        assert bot.send_message.call_count == 3

    async def test_no_edit_message_text_called(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_text = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("chunk 1")
        await editor.append_text("chunk 2")
        bot.edit_message_text.assert_not_called()

    async def test_append_tool_sends_indicator_message(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_tool("SearchTool")
        bot.send_message.assert_called_once()
        call_text = bot.send_message.call_args.kwargs["text"]
        # Tool names are normalized for display (shared with matrix/api since #114).
        assert "[TOOL: Search]" in call_text

    async def test_empty_text_not_sent(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        bot.send_message = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("")
        await editor.append_text("   ")
        bot.send_message.assert_not_called()
        assert editor.has_content is False

    async def test_finalize_is_noop(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_text = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("already sent")
        call_count_before = bot.send_message.call_count
        await editor.finalize("full text")
        # No additional sends or edits
        assert bot.send_message.call_count == call_count_before
        bot.edit_message_text.assert_not_called()

    async def test_finalize_no_messages_is_noop(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.edit_message_text = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.finalize("full text")
        bot.send_message.assert_not_called()
        bot.edit_message_text.assert_not_called()

    async def test_html_fallback_on_bad_request(self) -> None:
        from aiogram.exceptions import TelegramBadRequest

        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        # First call (HTML) fails, second (plain) succeeds
        bot.send_message = AsyncMock(
            side_effect=[TelegramBadRequest(MagicMock(), "bad HTML"), sent_msg],
        )

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("test text")
        assert bot.send_message.call_count == 2
        # Second call should be without parse_mode or None
        second_call = bot.send_message.call_args_list[1]
        assert second_call.kwargs.get("parse_mode") is None

    async def test_long_html_fallback_sends_complete_undelivered_plain_text(self) -> None:
        from aiogram.exceptions import TelegramBadRequest

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        html_attempts = 0

        async def send_message(**kwargs: object) -> Message:
            nonlocal html_attempts
            if kwargs["parse_mode"] == ParseMode.HTML:
                html_attempts += 1
                if html_attempts == 2:
                    raise TelegramBadRequest(MagicMock(), "bad HTML")
            return sent_msg

        bot.send_message = AsyncMock(side_effect=send_message)
        delivered = "A" * 4080
        remaining = "<&" + ("B" * 5000)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text(f"**{delivered}**\n\n{remaining}")

        plain_chunks = [
            call.kwargs["text"]
            for call in bot.send_message.call_args_list
            if call.kwargs["parse_mode"] is None
        ]
        assert html_attempts == 2
        assert "".join(plain_chunks) == f"\n\n{remaining}"
        assert all(0 < len(chunk) <= 4096 for chunk in plain_chunks)
        assert delivered not in "".join(plain_chunks)

    async def test_first_html_chunk_failure_falls_back_without_truncation(self) -> None:
        from aiogram.exceptions import TelegramBadRequest

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(
            side_effect=[TelegramBadRequest(MagicMock(), "bad HTML"), sent_msg, sent_msg]
        )
        text = "<&" + ("B" * 5000)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text(text)

        plain_chunks = [
            call.kwargs["text"]
            for call in bot.send_message.call_args_list
            if call.kwargs["parse_mode"] is None
        ]
        assert "".join(plain_chunks) == text
        assert all(len(chunk) <= 4096 for chunk in plain_chunks)

    async def test_markdown_formatting_applied(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("Use `code` and **bold** and *italic*")
        call_text = bot.send_message.call_args.kwargs["text"]
        assert "<code>code</code>" in call_text
        assert "<b>bold</b>" in call_text
        assert "<i>italic</i>" in call_text


class TestStreamEditorButtons:
    """Test button keyboard attachment in append-mode finalize."""

    async def test_finalize_with_buttons_attaches_keyboard(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        type(sent_msg).message_id = PropertyMock(return_value=99)
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_reply_markup = AsyncMock()

        reply_msg = MagicMock(spec=Message)
        reply_msg.answer = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1, reply_to=reply_msg)
        await editor.append_text("Choose one")
        await editor.finalize("Choose one\n\n[button:Yes] [button:No]")
        bot.edit_message_reply_markup.assert_called_once()
        call_kwargs = bot.edit_message_reply_markup.call_args.kwargs
        assert call_kwargs["chat_id"] == 1
        assert call_kwargs["message_id"] == 99
        markup = call_kwargs["reply_markup"]
        assert len(markup.inline_keyboard) == 1
        assert markup.inline_keyboard[0][0].text == "Yes"
        assert markup.inline_keyboard[0][1].text == "No"

    async def test_finalize_without_buttons_no_keyboard(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_reply_markup = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("No buttons here")
        await editor.finalize("No buttons here")
        bot.edit_message_reply_markup.assert_not_called()

    async def test_finalize_no_messages_sent_no_keyboard(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.finalize("[button:Ghost]")
        bot.send_message.assert_called_once()
        bot.edit_message_reply_markup.assert_called_once()

    async def test_tool_only_stream_sends_final_body(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_tool("SearchTool")
        await editor.finalize("Final answer")

        assert bot.send_message.call_count == 2
        assert bot.send_message.call_args.kwargs["text"] == "Final answer"

    async def test_progress_only_stream_with_buttons_sends_body_then_keyboard(self) -> None:
        bot = MagicMock()
        progress_msg = MagicMock(spec=Message)
        final_msg = MagicMock(spec=Message)
        type(final_msg).message_id = PropertyMock(return_value=102)
        bot.send_message = AsyncMock(side_effect=[progress_msg, final_msg])
        bot.edit_message_reply_markup = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_system("Working")
        await editor.finalize("Done\n[button:OK]")

        assert bot.send_message.call_count == 2
        assert bot.send_message.call_args.kwargs["text"] == "Done"
        assert bot.edit_message_reply_markup.call_args.kwargs["message_id"] == 102

    async def test_tool_only_empty_final_response_sends_placeholder(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_tool("SearchTool")
        await editor.finalize("")

        assert bot.send_message.call_count == 2
        assert "No final text response was returned" in bot.send_message.call_args.kwargs["text"]

    async def test_keyboard_on_last_message_after_multiple_chunks(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        msg1 = MagicMock(spec=Message)
        type(msg1).message_id = PropertyMock(return_value=10)
        msg2 = MagicMock(spec=Message)
        type(msg2).message_id = PropertyMock(return_value=20)
        bot.send_message = AsyncMock(side_effect=[msg1, msg2])
        bot.edit_message_reply_markup = AsyncMock()

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("First chunk")
        await editor.append_text("Second chunk")
        await editor.finalize("Full text\n\n[button:Done]")
        # Keyboard should be on the LAST message (msg2, id=20)
        call_kwargs = bot.edit_message_reply_markup.call_args.kwargs
        assert call_kwargs["message_id"] == 20

    async def test_finalize_buttons_with_reply_to(self) -> None:
        from ductor_bot.messenger.telegram.streaming import StreamEditor

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        type(sent_msg).message_id = PropertyMock(return_value=55)
        reply_msg = MagicMock(spec=Message)
        reply_msg.answer = AsyncMock(return_value=sent_msg)
        bot.edit_message_reply_markup = AsyncMock()

        editor = StreamEditor(bot, chat_id=1, reply_to=reply_msg)
        await editor.append_text("Content")
        await editor.finalize("Content\n[button:Go]")
        bot.edit_message_reply_markup.assert_called_once()
        assert bot.edit_message_reply_markup.call_args.kwargs["message_id"] == 55


class TestStreamEditorThreadId:
    """Test thread_id propagation in append-mode streaming."""

    async def test_thread_id_passed_to_send_message(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1, thread_id=77)
        await editor.append_text("Hello")
        assert bot.send_message.call_args.kwargs["message_thread_id"] == 77

    async def test_thread_id_none_by_default(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("Hello")
        assert bot.send_message.call_args.kwargs.get("message_thread_id") is None

    async def test_second_message_uses_thread_id(self) -> None:
        """When reply_to is used, answer() auto-propagates.
        The second message via send_message must use thread_id.
        """
        bot = MagicMock()
        reply_msg = MagicMock(spec=Message)
        sent_msg = MagicMock(spec=Message)
        reply_msg.answer = AsyncMock(return_value=sent_msg)
        bot.send_message = AsyncMock(return_value=sent_msg)

        editor = StreamEditor(bot, chat_id=1, reply_to=reply_msg, thread_id=77)
        await editor.append_text("First")
        await editor.append_text("Second")
        assert bot.send_message.call_args.kwargs["message_thread_id"] == 77


class TestStreamEditorTransientFailures:
    async def test_network_error_retries_complete_text_on_finalize(self) -> None:
        from aiogram.exceptions import TelegramNetworkError

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        bot.send_message = AsyncMock(
            side_effect=[
                TelegramNetworkError(MagicMock(), "network down"),
                sent_msg,
            ]
        )

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text("Complete answer")
        await editor.finalize("Complete answer")

        assert editor.has_content is True
        assert bot.send_message.await_count == 2
        assert bot.send_message.await_args_list[-1].kwargs["text"] == "Complete answer"

    async def test_partial_multi_chunk_failure_retries_complete_final_text(self) -> None:
        from aiogram.exceptions import TelegramNetworkError

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        final_text = "A" * 5000
        bot.send_message = AsyncMock(
            side_effect=[
                sent_msg,
                TelegramNetworkError(MagicMock(), "network down"),
                sent_msg,
                sent_msg,
            ]
        )

        editor = StreamEditor(bot, chat_id=1)
        await editor.append_text(final_text)
        await editor.finalize(final_text)

        assert bot.send_message.await_count == 4
        final_calls = bot.send_message.await_args_list[-2:]
        assert "".join(call.kwargs["text"] for call in final_calls) == final_text

    async def test_retry_after_waits_and_retries_once(self) -> None:
        from aiogram.exceptions import TelegramRetryAfter

        bot = MagicMock()
        sent_msg = MagicMock(spec=Message)
        retry = TelegramRetryAfter(MagicMock(), "rate limited", retry_after=10)
        bot.send_message = AsyncMock(side_effect=[retry, sent_msg])

        editor = StreamEditor(bot, chat_id=1)
        with patch(
            "ductor_bot.messenger.telegram.streaming.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
            await editor.append_text("Complete answer")

        sleep.assert_awaited_once_with(10)
        assert editor.has_content is True
        assert bot.send_message.await_count == 2

    async def test_repeated_retry_after_is_bounded(self) -> None:
        from aiogram.exceptions import TelegramRetryAfter

        bot = MagicMock()
        retry = TelegramRetryAfter(MagicMock(), "rate limited", retry_after=10)
        bot.send_message = AsyncMock(
            side_effect=[
                retry,
                TelegramRetryAfter(MagicMock(), "still limited", retry_after=20),
                MagicMock(spec=Message),
            ]
        )

        editor = StreamEditor(bot, chat_id=1)
        with patch(
            "ductor_bot.messenger.telegram.streaming.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
            await editor.append_text("Complete answer")

        sleep.assert_awaited_once_with(10)
        assert editor.has_content is False
        assert bot.send_message.await_count == 2
