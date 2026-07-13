"""MiMo credential resolution for the Anthropic-compatible Claude Code gateway."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from ductor_bot.infra.env_secrets import load_env_secrets

MIMO_DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/anthropic"

_NULLISH = frozenset({"", "none", "null", "nil"})


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    return "" if cleaned.lower() in _NULLISH else cleaned


def _root_ductor_home(path: Path) -> Path:
    if path.name == "workspace":
        path = path.parent
    if path.parent.name == "agents":
        path = path.parent.parent
    return path


def _configured_key(ductor_home: Path) -> str:
    config_path = ductor_home / "config" / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return _clean(data.get("mimo_api_key"))


def resolve_mimo_credentials(
    *,
    config_key: str | None = None,
    ductor_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Return ``(api_key, base_url)`` using one precedence order everywhere."""
    source_env = os.environ if environ is None else environ
    if ductor_home is None:
        configured_home = _clean(source_env.get("DUCTOR_HOME"))
        ductor_home = (
            Path(configured_home).expanduser() if configured_home else Path.home() / ".ductor"
        )
    root_home = _root_ductor_home(ductor_home)
    file_env = load_env_secrets(root_home / ".env")

    key = (
        _clean(config_key)
        or _clean(source_env.get("MIMO_API_KEY"))
        or _clean(file_env.get("MIMO_API_KEY"))
        or _configured_key(root_home)
    )
    base_url = (
        _clean(source_env.get("MIMO_BASE_URL"))
        or _clean(file_env.get("MIMO_BASE_URL"))
        or MIMO_DEFAULT_BASE_URL
    )
    return key, base_url


def build_mimo_gateway_env(
    *,
    config_key: str | None = None,
    model: str | None = "mimo-v2.5-pro",
    ductor_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Map MiMo credentials to the variables consumed by Claude Code."""
    model = _clean(model) or "mimo-v2.5-pro"
    key, base_url = resolve_mimo_credentials(
        config_key=config_key,
        ductor_home=ductor_home,
        environ=environ,
    )
    return {
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
    }
