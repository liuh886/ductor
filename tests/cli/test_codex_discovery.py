"""Tests for dynamic Codex model discovery."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ductor_bot.cli.codex_discovery import _INPUT, CodexModelInfo, discover_codex_models

_INIT_RESPONSE = json.dumps(
    {
        "id": 1,
        "result": {"userAgent": "ductor/0.98.0"},
    }
)

_MODEL_LIST_RESPONSE = json.dumps(
    {
        "id": 2,
        "result": {
            "data": [
                {
                    "id": "gpt-5.2-codex",
                    "displayName": "gpt-5.2-codex",
                    "description": "Frontier agentic coding model.",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fast"},
                        {"reasoningEffort": "medium", "description": "Balanced"},
                        {"reasoningEffort": "high", "description": "Deep"},
                        {"reasoningEffort": "xhigh", "description": "Extra deep"},
                    ],
                    "defaultReasoningEffort": "medium",
                    "isDefault": True,
                },
                {
                    "id": "gpt-5.1-codex-mini",
                    "displayName": "gpt-5.1-codex-mini",
                    "description": "Cheaper, faster.",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium", "description": "Default"},
                        {"reasoningEffort": "high", "description": "Deep"},
                    ],
                    "defaultReasoningEffort": "medium",
                    "isDefault": False,
                },
            ],
            "nextCursor": None,
        },
    }
)

_STDOUT = f"{_INIT_RESPONSE}\n{_MODEL_LIST_RESPONSE}\n"
_DEBUG_MODELS_RESPONSE = json.dumps(
    {
        "models": [
            {
                "slug": "gpt-5.4",
                "display_name": "gpt-5.4",
                "description": "Strong model for everyday coding.",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": "low"},
                    {"effort": "medium"},
                    {"effort": "high"},
                    {"effort": "xhigh"},
                ],
                "priority": 1,
            }
        ]
    }
)


def _mock_process(stdout: str = _STDOUT, returncode: int = 0) -> AsyncMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = MagicMock()
    lines = [f"{line}\n".encode() for line in stdout.splitlines()] + [b""]
    proc.stdout.readline = AsyncMock(side_effect=lines)
    proc.communicate = AsyncMock(return_value=(stdout.encode(), b""))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


async def test_discover_models_parses_response() -> None:
    proc = _mock_process()
    with (
        patch("ductor_bot.cli.codex_discovery.which", return_value="/usr/bin/codex"),
        patch("asyncio.create_subprocess_exec", return_value=proc),
    ):
        models = await discover_codex_models()

    assert len(models) == 2

    first = models[0]
    assert first.id == "gpt-5.2-codex"
    assert first.display_name == "gpt-5.2-codex"
    assert first.supported_efforts == ("low", "medium", "high", "xhigh")
    assert first.default_effort == "medium"
    assert first.is_default is True

    second = models[1]
    assert second.id == "gpt-5.1-codex-mini"
    assert second.supported_efforts == ("medium", "high")
    assert second.is_default is False
    proc.communicate.assert_awaited_once_with(input=_INPUT.encode())


async def test_discover_models_codex_not_installed() -> None:
    with patch("ductor_bot.cli.codex_discovery.which", return_value=None):
        models = await discover_codex_models()
    assert models == []


async def test_discover_models_timeout() -> None:
    async def _hang() -> bytes:
        await asyncio.sleep(1)
        return b""

    proc = _mock_process(stdout="")
    proc.stdout.readline = AsyncMock(side_effect=_hang)
    proc.kill = MagicMock()

    with (
        patch("ductor_bot.cli.codex_discovery.which", return_value="/usr/bin/codex"),
        patch("asyncio.create_subprocess_exec", return_value=proc),
    ):
        models = await discover_codex_models(deadline=0.1)

    assert models == []
    proc.kill.assert_called()


async def test_discover_models_invalid_json() -> None:
    proc = _mock_process(stdout="not json at all\n")
    with (
        patch("ductor_bot.cli.codex_discovery.which", return_value="/usr/bin/codex"),
        patch("asyncio.create_subprocess_exec", return_value=proc),
    ):
        models = await discover_codex_models()
    assert models == []


async def test_discover_models_logs_stdout_preview_on_missing_response(caplog) -> None:
    proc = _mock_process(stdout='{"id":1,"result":{"ok":true}}\n')
    with (
        patch("ductor_bot.cli.codex_discovery.which", return_value="/usr/bin/codex"),
        patch("asyncio.create_subprocess_exec", return_value=proc),
        caplog.at_level("WARNING"),
    ):
        models = await discover_codex_models()

    assert models == []
    assert 'No model/list response found in codex app-server output: {"id":1,"result":{"ok":true}}' in caplog.text


async def test_discover_models_falls_back_to_debug_models() -> None:
    app_proc = _mock_process(stdout='{"id":1,"result":{"ok":true}}\n')
    debug_proc = _mock_process(stdout=_DEBUG_MODELS_RESPONSE)
    with (
        patch("ductor_bot.cli.codex_discovery.which", return_value="/usr/bin/codex"),
        patch("asyncio.create_subprocess_exec", side_effect=[app_proc, debug_proc]),
    ):
        models = await discover_codex_models()

    assert len(models) == 1
    assert models[0].id == "gpt-5.4"
    assert models[0].supported_efforts == ("low", "medium", "high", "xhigh")
    assert models[0].default_effort == "medium"


async def test_discover_models_spawn_error() -> None:
    with (
        patch("ductor_bot.cli.codex_discovery.which", return_value="/usr/bin/codex"),
        patch("asyncio.create_subprocess_exec", side_effect=OSError("exec failed")),
    ):
        models = await discover_codex_models()
    assert models == []


async def test_codex_model_info_is_frozen() -> None:
    info = CodexModelInfo(
        id="test",
        display_name="Test",
        description="A test model",
        supported_efforts=("medium", "high"),
        default_effort="medium",
        is_default=False,
    )
    with pytest.raises(AttributeError):
        info.id = "changed"  # type: ignore[misc]
