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
_CJK_QUERY_PREFIXES = (
    "不存在的",
    "请帮我",
    "帮我",
    "查找",
    "搜索",
    "查询",
    "了解",
    "看看",
    "关于",
    "请",
    "和",
)
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


def _contains_cjk(term: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", term))


def _search_terms(terms: list[str]) -> tuple[list[str], list[str]]:
    """Return independent English terms and overlap-scored CJK grams."""
    english = _maximal_terms([term for term in terms if not _contains_cjk(term)])
    cjk = list(dict.fromkeys(term for term in terms if _contains_cjk(term) and 2 <= len(term) <= 4))
    if not cjk:
        cjk = _maximal_terms([term for term in terms if _contains_cjk(term)])
    return english, cjk


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


def _qualifies(
    *,
    english_matches: int,
    english_count: int,
    cjk_matches: int,
    cjk_count: int,
    cjk_title_matches: int,
) -> bool:
    english_ok = bool(english_count) and english_matches >= _minimum_matches(english_count)
    cjk_required = max(2, math.ceil(cjk_count * 0.1))
    cjk_ok = bool(cjk_count) and (
        cjk_matches >= cjk_required or (cjk_title_matches >= 1 and cjk_matches >= 2)
    )
    return english_ok or cjk_ok


def _search_rows(index_path: Path, terms: list[str], *, limit: int) -> list[dict[str, str]]:
    english_terms, cjk_terms = _search_terms(terms)
    query_terms = [*english_terms, *cjk_terms]
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

        candidates: dict[str, tuple[dict[str, str], float]] = {}
        for row in rows:
            payload = {key: str(row[key] or "") for key in ("title", "content", "path", "ulid")}
            candidates[payload["ulid"]] = (payload, float(row["fts_score"]))

        like_terms = query_terms if fts_failed else cjk_terms
        if like_terms:
            escaped_terms = [
                term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                for term in like_terms
            ]
            like_rows = connection.execute(
                """
                SELECT n.title, n.content, n.path, n.ulid,
                       SUM(CASE
                           WHEN n.title LIKE '%' || terms.value || '%' ESCAPE char(92)
                             OR n.content LIKE '%' || terms.value || '%' ESCAPE char(92)
                           THEN 1 ELSE 0 END) AS lexical_hits
                FROM notes n
                JOIN json_each(?) terms
                WHERE n.title LIKE '%' || terms.value || '%' ESCAPE char(92)
                   OR n.content LIKE '%' || terms.value || '%' ESCAPE char(92)
                GROUP BY n.ulid
                ORDER BY lexical_hits DESC, n.path, n.ulid
                LIMIT ?
                """,
                (json.dumps(escaped_terms, ensure_ascii=False), max(200, limit * 50)),
            ).fetchall()
            for row in like_rows:
                payload = {key: str(row[key] or "") for key in ("title", "content", "path", "ulid")}
                candidates.setdefault(payload["ulid"], (payload, 0.0))

        ranked: list[tuple[int, float, int, float, dict[str, str]]] = []
        for payload, fts_score in candidates.values():
            title = payload["title"].casefold()
            searchable = f"{payload['title']}\n{payload['content']}".casefold()
            english_matches = sum(_term_matches(term, searchable) for term in english_terms)
            cjk_matches = sum(_term_matches(term, searchable) for term in cjk_terms)
            english_title_matches = sum(_term_matches(term, title) for term in english_terms)
            cjk_title_matches = sum(_term_matches(term, title) for term in cjk_terms)
            if not _qualifies(
                english_matches=english_matches,
                english_count=len(english_terms),
                cjk_matches=cjk_matches,
                cjk_count=len(cjk_terms),
                cjk_title_matches=cjk_title_matches,
            ):
                continue
            title_matches = english_title_matches + cjk_title_matches
            match_count = english_matches + cjk_matches
            coverage = english_matches / max(1, len(english_terms)) + cjk_matches / max(
                1, len(cjk_terms)
            )
            ranked.append((title_matches, coverage, match_count, fts_score, payload))
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        return [payload for _title, _coverage, _matches, _score, payload in ranked[:limit]]
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
