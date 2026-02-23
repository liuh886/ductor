"""Tests for CLI auth detection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.cli.auth import (
    AuthResult,
    AuthStatus,
    check_claude_auth,
    check_codex_auth,
    check_gemini_auth,
    format_age,
    gemini_uses_api_key_mode,
)

if TYPE_CHECKING:
    import pytest


def test_auth_status_values() -> None:
    assert AuthStatus.AUTHENTICATED.value == "authenticated"
    assert AuthStatus.INSTALLED.value == "installed"
    assert AuthStatus.NOT_FOUND.value == "not_found"


def test_auth_result_is_authenticated() -> None:
    result = AuthResult(provider="claude", status=AuthStatus.AUTHENTICATED)
    assert result.is_authenticated is True


def test_auth_result_not_authenticated() -> None:
    result = AuthResult(provider="claude", status=AuthStatus.INSTALLED)
    assert result.is_authenticated is False


def test_auth_result_age_human_none() -> None:
    result = AuthResult(provider="claude", status=AuthStatus.NOT_FOUND)
    assert result.age_human == ""


def test_format_age_seconds() -> None:
    from datetime import UTC, datetime, timedelta

    dt = datetime.now(UTC) - timedelta(seconds=30)
    assert format_age(dt) == "30s ago"


def test_format_age_minutes() -> None:
    from datetime import UTC, datetime, timedelta

    dt = datetime.now(UTC) - timedelta(minutes=5)
    assert format_age(dt) == "5m ago"


def test_format_age_hours() -> None:
    from datetime import UTC, datetime, timedelta

    dt = datetime.now(UTC) - timedelta(hours=3)
    assert format_age(dt) == "3h ago"


def test_format_age_days() -> None:
    from datetime import UTC, datetime, timedelta

    dt = datetime.now(UTC) - timedelta(days=2)
    assert format_age(dt) == "2d ago"


def test_check_claude_auth_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = check_claude_auth()
    assert result.status == AuthStatus.NOT_FOUND


def test_check_claude_auth_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".claude").mkdir()
    result = check_claude_auth()
    assert result.status == AuthStatus.INSTALLED


def test_check_claude_auth_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text("{}")
    result = check_claude_auth()
    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file is not None


def test_check_codex_auth_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    result = check_codex_auth()
    assert result.status == AuthStatus.NOT_FOUND


def test_check_codex_auth_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(codex_dir))
    result = check_codex_auth()
    assert result.status == AuthStatus.AUTHENTICATED


# -- Gemini auth --


def test_check_gemini_auth_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.setattr(
        _auth_mod,
        "find_gemini_cli",
        lambda: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    result = check_gemini_auth()
    assert result.status == AuthStatus.NOT_FOUND


def test_check_gemini_auth_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    result = check_gemini_auth()
    assert result.status == AuthStatus.INSTALLED


def test_check_gemini_auth_authenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    result = check_gemini_auth()
    assert result.status == AuthStatus.AUTHENTICATED


def test_check_gemini_auth_google_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    result = check_gemini_auth()
    assert result.status == AuthStatus.AUTHENTICATED


def test_check_gemini_auth_oauth_creds_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    oauth = gemini_home / "oauth_creds.json"
    oauth.write_text('{"access_token":"x"}')

    result = check_gemini_auth()

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file == oauth


def test_check_gemini_auth_dotenv_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    dotenv = gemini_home / ".env"
    dotenv.write_text("GEMINI_API_KEY=test-from-dotenv\n")

    result = check_gemini_auth()

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file == dotenv


def test_check_gemini_auth_uses_gemini_cli_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "ignored-home")
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    custom_home = tmp_path / "custom-home"
    monkeypatch.setenv("GEMINI_CLI_HOME", str(custom_home))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    gemini_home = custom_home / ".gemini"
    gemini_home.mkdir(parents=True)
    oauth = gemini_home / "oauth_creds.json"
    oauth.write_text('{"access_token":"x"}')

    result = check_gemini_auth()

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file == oauth


def test_check_gemini_auth_oauth_selected_type_with_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}'
    )
    accounts = gemini_home / "google_accounts.json"
    accounts.write_text('{"active":"user@example.com","old":[]}')

    result = check_gemini_auth()

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file == accounts


def test_check_gemini_auth_selected_type_gemini_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    settings = gemini_home / "settings.json"
    settings.write_text('{"security":{"auth":{"selectedType":"gemini-api-key"}}}')

    result = check_gemini_auth()

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file == settings


def test_check_gemini_auth_ductor_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    ductor_config = tmp_path / ".ductor" / "config" / "config.json"
    ductor_config.parent.mkdir(parents=True)
    ductor_config.write_text('{"gemini_api_key":"from-ductor-config"}')

    result = check_gemini_auth()

    assert result.status == AuthStatus.AUTHENTICATED
    assert result.auth_file == ductor_config


def test_check_gemini_auth_ductor_config_null_string_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ductor_bot.cli.auth as _auth_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_auth_mod, "find_gemini_cli", lambda: "/usr/bin/gemini")
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    ductor_config = tmp_path / ".ductor" / "config" / "config.json"
    ductor_config.parent.mkdir(parents=True)
    ductor_config.write_text('{"gemini_api_key":"null"}')

    result = check_gemini_auth()

    assert result.status == AuthStatus.INSTALLED
    assert result.auth_file is None


def test_gemini_uses_api_key_mode_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"gemini-api-key"}}}'
    )

    assert gemini_uses_api_key_mode() is True


def test_gemini_uses_api_key_mode_false_for_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}'
    )

    assert gemini_uses_api_key_mode() is False
