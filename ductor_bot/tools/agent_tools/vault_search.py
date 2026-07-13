# ruff: noqa: INP001

"""Explicit, read-only search over the zhihaol vault index."""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
from pathlib import Path

from ductor_bot.workspace.paths import DuctorPaths, resolve_paths

logger = logging.getLogger(__name__)

_MESSAGE_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "already",
        "also",
        "and",
        "before",
        "between",
        "could",
        "does",
        "done",
        "find",
        "from",
        "have",
        "help",
        "here",
        "into",
        "just",
        "know",
        "like",
        "look",
        "make",
        "more",
        "need",
        "only",
        "other",
        "please",
        "should",
        "show",
        "some",
        "still",
        "such",
        "take",
        "tell",
        "than",
        "that",
        "their",
        "then",
        "there",
        "these",
        "they",
        "think",
        "this",
        "those",
        "through",
        "very",
        "want",
        "well",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
        "your",
    }
)
_CJK_QUERY_PREFIXES = ("请帮我", "帮我", "查找", "搜索", "查询", "了解", "看看", "关于", "请", "和")
_CJK_QUERY_SUFFIXES = ("相关资料", "相关信息", "资料", "信息", "内容", "情况", "相关")


def _clean_cjk_run(run: str) -> str:
    cleaned = run
    changed = True
    while changed:
        changed = False
        for prefix in _CJK_QUERY_PREFIXES:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 1:
                cleaned = cleaned.removeprefix(prefix)
                changed = True
                break
        for suffix in _CJK_QUERY_SUFFIXES:
            if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
                cleaned = cleaned.removesuffix(suffix)
                changed = True
                break
    return cleaned


def resolve_vault_index_path(paths: DuctorPaths) -> Path | None:
    """Return the root agent's neutral vault index, including for sub-agents."""
    root_home = (
        paths.ductor_home.parent.parent
        if paths.ductor_home.parent.name == "agents"
        else paths.ductor_home
    )
    index_path = root_home / "workspace" / "memory_system" / "vault_index.db"
    return index_path if index_path.is_file() else None


def extract_query_terms(text: str) -> list[str]:
    """Extract bounded English, identifier, and CJK terms from a query."""
    if not text:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = value.casefold().strip()
        if normalized and normalized not in seen and len(terms) < 64:
            seen.add(normalized)
            terms.append(normalized)

    for word in re.findall(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{2,}", text):
        if word.casefold() not in _MESSAGE_STOPWORDS:
            add(word)
    for raw_run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        run = _clean_cjk_run(raw_run)
        if len(run) <= 12:
            add(run)
        for width in (4, 3, 2):
            for index in range(len(run) - width + 1):
                add(run[index : index + width])
    return terms


def _maximal_terms(terms: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    return [
        term
        for term in normalized
        if not any(term != other and term in other for other in normalized)
    ]


def _fts_escape(term: str) -> str:
    cleaned = re.sub(r"[\"'*(){}:+]", "", term.strip()).replace("-", " ")
    for operator in ("AND", "OR", "NOT", "NEAR"):
        cleaned = cleaned.replace(operator, "")
    return cleaned.strip()


def _minimum_matches(term_count: int) -> int:
    if term_count <= 1:
        return term_count
    return max(2, math.ceil(term_count * 0.6))


def _term_matches(term: str, searchable: str) -> bool:
    normalized = _fts_escape(term).casefold()
    return bool(normalized) and normalized in searchable


def _search_rows(index_path: Path, terms: list[str], *, limit: int) -> list[dict[str, str]]:
    query_terms = _maximal_terms(terms)
    quoted = [
        f'"{escaped.replace(chr(34), chr(34) * 2)}"'
        for term in query_terms
        if (escaped := _fts_escape(term))
    ]
    if not quoted:
        return []

    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:{index_path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        fts_failed = False
        try:
            rows = connection.execute(
                """
                SELECT n.title, n.content, n.path, n.ulid,
                       bm25(notes_fts, 0.0, 5.0, 1.0) AS fts_score
                FROM notes_fts fts
                JOIN notes n ON n.ulid = fts.ulid
                WHERE notes_fts MATCH ?
                ORDER BY bm25(notes_fts, 0.0, 5.0, 1.0)
                LIMIT ?
                """,
                (" OR ".join(quoted), max(50, limit * 10)),
            ).fetchall()
        except sqlite3.Error:
            fts_failed = True
            rows = []

        required_matches = _minimum_matches(len(query_terms))
        ranked: list[tuple[int, int, float, dict[str, str]]] = []
        for row in rows:
            payload = {key: str(row[key] or "") for key in ("title", "content", "path", "ulid")}
            title = payload["title"].casefold()
            searchable = f"{payload['title']}\n{payload['content']}".casefold()
            match_count = sum(_term_matches(term, searchable) for term in query_terms)
            if match_count < required_matches:
                continue
            title_matches = sum(_term_matches(term, title) for term in query_terms)
            ranked.append((title_matches, match_count, float(row["fts_score"]), payload))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        results = [payload for _title, _matches, _score, payload in ranked[:limit]]

        like_terms = (
            query_terms
            if fts_failed
            else [term for term in query_terms if re.search(r"[\u3400-\u9fff]", term)]
        )
        seen = {row["ulid"] for row in results}
        if like_terms and len(results) < limit:
            escaped_terms = [
                term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                for term in like_terms
            ]
            like_rows = connection.execute(
                """
                SELECT DISTINCT n.title, n.content, n.path, n.ulid
                FROM notes n
                JOIN json_each(?) terms
                WHERE n.title LIKE '%' || terms.value || '%' ESCAPE char(92)
                   OR n.content LIKE '%' || terms.value || '%' ESCAPE char(92)
                ORDER BY n.path, n.ulid
                LIMIT ?
                """,
                (json.dumps(escaped_terms, ensure_ascii=False), limit - len(results)),
            ).fetchall()
            for row in like_rows:
                payload = {key: str(row[key] or "") for key in ("title", "content", "path", "ulid")}
                if payload["ulid"] not in seen:
                    results.append(payload)
                    seen.add(payload["ulid"])
        return results[:limit]
    except (OSError, sqlite3.Error):
        logger.debug("Vault index search failed", exc_info=True)
        return []
    finally:
        if connection is not None:
            connection.close()


def _excerpt(content: str, terms: list[str], *, max_chars: int) -> str:
    compact = " ".join(content.split())
    if len(compact) <= max_chars:
        return compact
    folded = compact.casefold()
    matches = [offset for term in terms if (offset := folded.find(term.casefold())) >= 0]
    center = min(matches) if matches else 0
    start = max(0, center - max_chars // 3)
    end = min(len(compact), start + max_chars)
    start = max(0, end - max_chars)
    return f"{'...' if start else ''}{compact[start:end]}{'...' if end < len(compact) else ''}"


def search_vault(
    query: str,
    *,
    paths: DuctorPaths | None = None,
    index_path: Path | None = None,
    limit: int = 5,
    excerpt_chars: int = 800,
) -> list[dict[str, str]]:
    """Return bounded source-backed passages without mutating any state."""
    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")
    if not 200 <= excerpt_chars <= 2000:
        raise ValueError("excerpt_chars must be between 200 and 2000")
    terms = extract_query_terms(query)
    resolved_index = index_path or resolve_vault_index_path(paths or resolve_paths())
    if resolved_index is None or not terms:
        return []
    rows = _search_rows(resolved_index, terms, limit=limit)
    return [
        {
            "title": row["title"].strip(),
            "path": row["path"].strip(),
            "excerpt": _excerpt(row["content"], terms, max_chars=excerpt_chars),
        }
        for row in rows
    ]


def _render_text(results: list[dict[str, str]]) -> str:
    if not results:
        return "No relevant vault notes found."
    return "\n\n".join(
        f"## {result['title'] or 'Vault note'}\n{result['excerpt']}\n_Source: {result['path']}_"
        for result in results
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the zhihaol vault index (read-only).")
    parser.add_argument("query", help="Keywords or a natural-language search query.")
    parser.add_argument("--limit", type=int, default=5, choices=range(1, 11))
    parser.add_argument("--excerpt-chars", type=int, default=800, choices=range(200, 2001))
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    args = parser.parse_args()
    results = search_vault(args.query, limit=args.limit, excerpt_chars=args.excerpt_chars)
    print(json.dumps(results, ensure_ascii=False, indent=2) if args.json else _render_text(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
