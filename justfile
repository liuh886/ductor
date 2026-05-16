# Development task runner for ductor
# Requires: just 1.42.0+ (https://github.com/casey/just)

min_version := "1.42.0"
current_version := `just --version | cut -d' ' -f2`
_check_version := if semver_matches(current_version, ">=" + min_version) == "true" { "" } else { error("just >= " + min_version + " required for [parallel]") }

# Auto-fix formatting and lint issues
fix:
    uv run ruff format .
    uv run ruff check --fix .

# Run all linters, type checks, i18n completeness, and tests (lanes run in parallel; tests stay sequential)
[parallel]
check: _lint _format _types _i18n _test

# Run the test suite sequentially (safe default; see `test-parallel` for opt-in)
test *args:
    uv run pytest {{args}}

# Run the test suite in parallel via pytest-xdist (opt-in; not verified parallel-safe across all 2246+ tests)
test-parallel *args:
    uv run pytest -n auto {{args}}

[private]
_lint:
    uv run ruff check .

[private]
_format:
    uv run ruff format --check .

[private]
_types:
    uv run mypy ductor_bot

[private]
_i18n:
    uv run python -m ductor_bot.i18n.check --quiet

[private]
_test:
    uv run pytest
