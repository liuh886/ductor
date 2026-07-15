"""Tests for the checked-in vault retrieval benchmark runner."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


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
        ("1", "CCUS Policy", "Carbon capture policy and storage monitoring.", "CCUS.md"),
        ("2", "香港优才计划", "香港人才申请包含评分标准和材料准备。", "HK.md"),
    ]
    connection.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)", rows)
    connection.executemany("INSERT INTO notes_fts VALUES (?, ?, ?)", (row[:3] for row in rows))
    connection.commit()
    connection.close()


def test_benchmark_reports_retrieval_and_abstention_metrics(tmp_path: Path) -> None:
    index = tmp_path / "vault_index.db"
    fixture = tmp_path / "cases.json"
    _build_index(index)
    fixture.write_text(
        json.dumps(
            {
                "version": 1,
                "limit": 5,
                "excerpt_chars": 200,
                "thresholds": {
                    "recall_at_k": 1.0,
                    "mrr": 1.0,
                    "abstention_precision": 1.0,
                    "difficulty_recall_at_k": {"lexical": 1.0, "paraphrase": 1.0},
                },
                "cases": [
                    {
                        "id": "ccus",
                        "query": "CCUS policy",
                        "difficulty": "lexical",
                        "expected_paths": ["CCUS.md"],
                    },
                    {
                        "id": "hk",
                        "query": "香港人才引进如何打分申请",
                        "difficulty": "paraphrase",
                        "expected_paths": ["HK.md"],
                    },
                    {
                        "id": "missing",
                        "query": "Zephyr Quantum Mango ZXQ-991",
                        "difficulty": "abstention",
                        "abstain": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.vault_retrieval_benchmark",
            "--fixture",
            str(fixture),
            "--index",
            str(index),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["metrics"]["recall_at_k"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
    assert report["metrics"]["abstention_precision"] == 1.0
    assert report["metrics"]["source_coverage"] == 1.0
    assert report["metrics"]["excerpt_compliance"] == 1.0
    assert report["metrics"]["latency_p50_ms"] >= 0
    assert report["metrics"]["latency_p95_ms"] >= report["metrics"]["latency_p50_ms"]
    assert report["metrics"]["by_difficulty"]["paraphrase"]["recall_at_k"] == 1.0
    assert report["checks"]["difficulty_recall_at_k.lexical"] is True
    assert report["checks"]["difficulty_recall_at_k.paraphrase"] is True
    assert [item["id"] for item in report["evidence"]] == ["ccus", "hk", "missing"]


def test_benchmark_rejects_an_empty_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "empty.json"
    index = tmp_path / "vault_index.db"
    fixture.write_text('{"version": 1, "cases": []}', encoding="utf-8")
    _build_index(index)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.vault_retrieval_benchmark",
            "--fixture",
            str(fixture),
            "--index",
            str(index),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 2
    assert "at least one positive case" in result.stderr


def test_benchmark_requires_an_abstention_case(tmp_path: Path) -> None:
    fixture = tmp_path / "positive-only.json"
    index = tmp_path / "vault_index.db"
    fixture.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "ccus",
                        "query": "CCUS policy",
                        "difficulty": "lexical",
                        "expected_paths": ["CCUS.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _build_index(index)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.vault_retrieval_benchmark",
            "--fixture",
            str(fixture),
            "--index",
            str(index),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 2
    assert "at least one abstention case" in result.stderr
