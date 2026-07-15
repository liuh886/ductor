"""Tests for explicit, read-only vault retrieval."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ductor_bot.tools.agent_tools.vault_search import (
    extract_query_terms,
    resolve_vault_index_path,
    search_vault,
)
from ductor_bot.workspace.paths import DuctorPaths


def _build_index(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE notes (
            ulid TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            path TEXT UNIQUE
        );
        CREATE VIRTUAL TABLE notes_fts USING fts5(
            ulid UNINDEXED, title, content, tokenize='porter unicode61'
        );
        """
    )
    rows = [
        (
            "1",
            "CCUS Policy",
            "Carbon capture policy and commercial viability.",
            "300_Resources/CCUS.md",
        ),
        ("2", "香港优才计划", "香港优才计划的申请要求和材料清单。", "100_Project/香港优才.md"),
        ("3", "Unrelated", "A grocery list for the weekend.", "000_Inbox/grocery.md"),
        (
            "4",
            "香港优秀人才入境计划",
            "香港人才申请指南包含资格要求、评分标准和材料准备。",
            "100_Project/香港人才申请.md",
        ),
        (
            "5",
            "General protocol notes",
            "某些项目不存在统一协议,需要根据实际情况判断。",
            "300_Resources/protocol.md",
        ),
    ]
    connection.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)", rows)
    connection.executemany("INSERT INTO notes_fts VALUES (?, ?, ?)", (row[:3] for row in rows))
    connection.commit()
    connection.close()


def test_extract_query_terms_supports_english_and_chinese() -> None:
    terms = extract_query_terms("Please find CCUS 和香港优才计划资料")
    assert "ccus" in terms
    assert "香港优才计划" in terms


def test_search_vault_returns_bounded_source_backed_results(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    _build_index(index)

    results = search_vault("CCUS commercial viability", index_path=index, excerpt_chars=200)

    assert results[0]["path"] == "300_Resources/CCUS.md"
    assert len(results[0]["excerpt"]) <= 206


def test_search_vault_uses_chinese_fallback(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    _build_index(index)

    results = search_vault("香港优才计划", index_path=index)

    assert results[0]["path"] == "100_Project/香港优才.md"


def test_search_vault_ranks_partial_chinese_paraphrase(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    _build_index(index)

    results = search_vault("香港人才引进如何打分申请", index_path=index)

    assert results[0]["path"] == "100_Project/香港人才申请.md"


def test_search_vault_abstains_for_unrelated_chinese_query(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    _build_index(index)

    assert search_vault("量子火星传送协议", index_path=index) == []


def test_search_vault_ignores_fictional_cjk_query_prefix(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    _build_index(index)

    assert search_vault("不存在的紫色量子香蕉协议", index_path=index) == []


def test_search_vault_abstains_for_unrelated_query(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    _build_index(index)
    assert search_vault("invented quasar protocol", index_path=index) == []


def test_subagent_resolves_root_vault_index(tmp_path: Path) -> None:
    root = tmp_path / ".ductor"
    index = root / "workspace" / "memory_system" / "vault_index.db"
    index.parent.mkdir(parents=True)
    index.touch()
    paths = DuctorPaths(ductor_home=root / "agents" / "researcher")
    assert resolve_vault_index_path(paths) == index
