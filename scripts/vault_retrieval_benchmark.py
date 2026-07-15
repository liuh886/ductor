"""Benchmark source-backed vault retrieval against a versioned JSON fixture."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from ductor_bot.tools.agent_tools.vault_search import search_vault


def _ratio(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _threshold_checks(metrics: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for name, value in thresholds.items():
        if name == "difficulty_recall_at_k":
            for difficulty, threshold in dict(value).items():
                difficulty_metrics = metrics["by_difficulty"].get(str(difficulty), {})
                checks[f"{name}.{difficulty}"] = float(
                    difficulty_metrics.get("recall_at_k", 0.0)
                ) >= float(threshold)
        else:
            checks[name] = float(metrics[name]) >= float(value)
    return checks


def run_benchmark(fixture_path: Path, index_path: Path) -> dict[str, Any]:
    """Run all fixture cases against one immutable vault index."""
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    limit = int(fixture.get("limit", 5))
    excerpt_chars = int(fixture.get("excerpt_chars", 800))
    cases = list(fixture["cases"])
    if not any(not bool(case.get("abstain", False)) for case in cases):
        raise ValueError("fixture must contain at least one positive case")
    if not any(bool(case.get("abstain", False)) for case in cases):
        raise ValueError("fixture must contain at least one abstention case")
    evidence: list[dict[str, Any]] = []
    positive_hits = 0
    reciprocal_rank = 0.0
    positive_count = 0
    abstention_hits = 0
    abstention_count = 0
    result_count = 0
    sourced_result_count = 0
    compliant_excerpt_count = 0
    latencies: list[float] = []
    difficulty_scores: dict[str, list[tuple[bool, float]]] = defaultdict(list)

    for case in cases:
        started = time.perf_counter()
        results = search_vault(
            str(case["query"]),
            index_path=index_path,
            limit=limit,
            excerpt_chars=excerpt_chars,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        paths = [result["path"] for result in results]
        result_count += len(results)
        sourced_result_count += sum(bool(result["path"].strip()) for result in results)
        compliant_excerpt_count += sum(
            len(result["excerpt"]) <= excerpt_chars + 6 for result in results
        )
        abstain = bool(case.get("abstain", False))
        rank = 0
        if abstain:
            abstention_count += 1
            abstention_hits += not paths
        else:
            positive_count += 1
            expected = set(case["expected_paths"])
            rank = next(
                (position for position, path in enumerate(paths, start=1) if path in expected),
                0,
            )
            hit = rank > 0
            positive_hits += hit
            reciprocal_rank += 1 / rank if rank else 0.0
            difficulty_scores[str(case["difficulty"])].append((hit, 1 / rank if rank else 0.0))
        evidence.append(
            {
                "id": str(case["id"]),
                "query": str(case["query"]),
                "difficulty": str(case["difficulty"]),
                "abstain": abstain,
                "rank": rank,
                "paths": paths,
                "latency_ms": round(latency_ms, 3),
            }
        )

    ordered_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(len(ordered_latencies) * 0.95) - 1)
    metrics = {
        "recall_at_k": _ratio(positive_hits, positive_count),
        "mrr": _ratio(reciprocal_rank, positive_count),
        "abstention_precision": _ratio(abstention_hits, abstention_count),
        "source_coverage": _ratio(sourced_result_count, result_count),
        "excerpt_compliance": _ratio(compliant_excerpt_count, result_count),
        "latency_p50_ms": statistics.median(ordered_latencies),
        "latency_p95_ms": ordered_latencies[p95_index],
        "positive_cases": positive_count,
        "abstention_cases": abstention_count,
        "total_cases": len(cases),
        "by_difficulty": {
            difficulty: {
                "recall_at_k": _ratio(sum(hit for hit, _rank in scores), len(scores)),
                "mrr": _ratio(sum(rank for _hit, rank in scores), len(scores)),
                "cases": len(scores),
            }
            for difficulty, scores in sorted(difficulty_scores.items())
        },
    }
    thresholds = dict(fixture.get("thresholds", {}))
    checks = _threshold_checks(metrics, thresholds)
    return {
        "version": int(fixture.get("version", 1)),
        "adapter": "BuiltinVaultAdapter",
        "limit": limit,
        "excerpt_chars": excerpt_chars,
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_benchmark(args.fixture.resolve(), args.index.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Invalid benchmark fixture: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
