"""Shared formatting primitives for command response text."""

from __future__ import annotations

SEP = "\u2500\u2500\u2500"


def fmt(*blocks: str) -> str:
    """Join non-empty blocks with double newlines."""
    return "\n\n".join(b for b in blocks if b)


# -- Shared response texts (eliminate duplication between handlers.py / commands.py) --

SESSION_ERROR_TEXT = fmt(
    "**Session Error**",
    SEP,
    "[{model}] An error occurred.\n"
    "Your session has been preserved -- send another message to retry.\n"
    "Use /new to start a fresh session if the problem persists.",
)

# Known CLI error patterns -> user-friendly short explanation.
_AUTH_PATTERNS = (
    "401",
    "unauthorized",
    "authentication",
    "signing in again",
    "sign in again",
    "token has been",
)
_RATE_PATTERNS = ("429", "rate limit", "too many requests", "quota exceeded")
_CONTEXT_PATTERNS = ("context length", "token limit", "maximum context", "too long")


def classify_cli_error(raw: str) -> str | None:
    """Return a user-facing hint for known CLI error patterns, or None."""
    lower = raw.lower()
    if any(p in lower for p in _AUTH_PATTERNS):
        return "Authentication failed. Please re-authenticate the CLI (e.g. `codex auth` or check your API key)."
    if any(p in lower for p in _RATE_PATTERNS):
        return "Rate limit reached. Wait a moment and try again."
    if any(p in lower for p in _CONTEXT_PATTERNS):
        return "Context length exceeded. Use /new to start a fresh session."
    return None


def session_error_text(model: str, cli_detail: str = "") -> str:
    """Build the error message shown to the user on CLI failure."""
    base = SESSION_ERROR_TEXT.format(model=model)
    hint = classify_cli_error(cli_detail) if cli_detail else None
    if hint:
        return fmt(base, f"**Cause:** {hint}")
    if cli_detail:
        # Show first meaningful line, truncated.
        detail = cli_detail.strip().split("\n")[0][:200]
        return fmt(base, f"**Detail:** `{detail}`")
    return base


def new_session_text(provider: str) -> str:
    """Build /new response for provider-local reset."""
    provider_label = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}.get(
        provider.lower(), provider
    )
    return fmt(
        "**Session Reset**",
        SEP,
        f"Session reset for {provider_label} in this chat only.\n"
        "Other provider sessions were preserved.\n"
        "Send a message to continue.",
    )


def stop_text(killed: bool, provider: str) -> str:
    """Build the /stop response."""
    if killed:
        body = f"{provider} terminated. All queued messages discarded."
    else:
        body = "Nothing running right now."
    return fmt("**Agent Stopped**", SEP, body)
