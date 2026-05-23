"""Accuracy eval harness for the topic classifier.

Runs the pipeline against labeled HTML fixtures and reports top-1 / top-3
hit-rate and MRR. Aggregate metrics are compared against `baseline.json`;
the test fails if any metric regresses by more than `TOLERANCE`.

To refresh the baseline after an intentional change:
    .local/venv/bin/python -m tests.eval.test_topic_accuracy --update-baseline
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from crawler.api.schemas import Topic
from crawler.pipeline import process_html

EVAL_DIR = Path(__file__).parent
FIXTURES_DIR = EVAL_DIR.parent / "fixtures"
CASES_PATH = EVAL_DIR / "fixtures.yaml"
BASELINE_PATH = EVAL_DIR / "baseline.json"
TOLERANCE = 0.05  # absolute, allows tiny variance but catches real regressions


@dataclass
class CaseResult:
    id: str
    top1: int
    top3_hit_rate: float
    mrr: float
    actual_top_3: list[str]


def _matches(actual: str, expected: str) -> bool:
    """Case-insensitive token-set + substring match.

    True when:
      - exact (case-insensitive) string match, or
      - one is a substring of the other (min 4 chars on the shorter side), or
      - they share a token of length >= 4.
    Handles "outdoor"/"outdoors" and "kitchen"/"kitchen toaster".
    """
    a = actual.lower().strip()
    e = expected.lower().strip()
    if not a or not e:
        return False
    if a == e:
        return True
    short, long = (a, e) if len(a) <= len(e) else (e, a)
    if len(short) >= 4 and short in long:
        return True
    a_tokens = {t for t in a.split() if len(t) >= 4}
    e_tokens = {t for t in e.split() if len(t) >= 4}
    return bool(a_tokens & e_tokens)


def _hits(expected: list[str], topics: list[Topic], k: int) -> int:
    actual = [t.label for t in topics[:k]]
    return sum(1 for exp in expected if any(_matches(a, exp) for a in actual))


def _first_relevant_rank(relevant: list[str], topics: list[Topic]) -> int | None:
    for idx, t in enumerate(topics, start=1):
        if any(_matches(t.label, r) for r in relevant):
            return idx
    return None


def _run_case(case: dict) -> CaseResult:
    html = (FIXTURES_DIR / case["fixture"]).read_text(encoding="utf-8")
    res = process_html(
        url=case["url"],
        html=html,
        http_status=200,
        content_type="text/html",
        fetcher_used="static",
    )
    expected = case["expected_top_3"]
    top3_hits = _hits(expected, res.topics, k=3)
    top1_hit = 1 if _hits(expected, res.topics, k=1) >= 1 else 0
    rank = _first_relevant_rank(case["relevant"], res.topics)
    return CaseResult(
        id=case["id"],
        top1=top1_hit,
        # Divide by actual count so cases with fewer than 3 expected labels still
        # score correctly (max rate stays 1.0).
        top3_hit_rate=round(top3_hits / max(len(expected), 1), 4),
        mrr=round(1.0 / rank, 4) if rank else 0.0,
        actual_top_3=[t.label for t in res.topics[:3]],
    )


def _aggregate(results: list[CaseResult]) -> dict[str, float]:
    n = len(results)
    return {
        "n": n,
        "top1": round(sum(r.top1 for r in results) / n, 4),
        "top3_hit_rate": round(sum(r.top3_hit_rate for r in results) / n, 4),
        "mrr": round(sum(r.mrr for r in results) / n, 4),
    }


def _load_cases() -> list[dict]:
    return yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _missing_fixtures(cases: list[dict]) -> list[str]:
    return [c["fixture"] for c in cases if not (FIXTURES_DIR / c["fixture"]).exists()]


def run_all() -> tuple[list[CaseResult], dict[str, float]]:
    cases = _load_cases()
    results = [_run_case(c) for c in cases]
    return results, _aggregate(results)


@pytest.fixture(scope="module")
def eval_run():
    cases = _load_cases()
    missing = _missing_fixtures(cases)
    if missing:
        pytest.skip(
            f"Eval fixtures missing ({len(missing)}/{len(cases)}): {missing[:3]}…  "
            "Run: .local/venv/bin/python scripts/download_eval_fixtures.py"
        )
    return run_all()


def test_classifier_accuracy_vs_baseline(eval_run, capsys):
    results, agg = eval_run
    baseline = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        if BASELINE_PATH.exists()
        else None
    )

    with capsys.disabled():
        print("\n=== Per-case ===")
        for r in results:
            print(
                f"  {r.id:20}  top1={r.top1}  top3={r.top3_hit_rate:.2f}  "
                f"mrr={r.mrr:.2f}  actual_top_3={r.actual_top_3}"
            )
        print(f"\n=== Aggregate (n={agg['n']}) ===")
        print(
            f"  top1={agg['top1']:.4f}  top3_hit_rate={agg['top3_hit_rate']:.4f}  "
            f"mrr={agg['mrr']:.4f}"
        )
        if baseline:
            print("\n=== Baseline ===")
            print(
                f"  top1={baseline['top1']:.4f}  "
                f"top3_hit_rate={baseline['top3_hit_rate']:.4f}  "
                f"mrr={baseline['mrr']:.4f}"
            )
            print("=== Delta ===")
            print(
                f"  top1={agg['top1'] - baseline['top1']:+.4f}  "
                f"top3_hit_rate={agg['top3_hit_rate'] - baseline['top3_hit_rate']:+.4f}  "
                f"mrr={agg['mrr'] - baseline['mrr']:+.4f}"
            )

    if baseline is None:
        pytest.skip(
            "No baseline.json present. Run: "
            ".local/venv/bin/python -m tests.eval.test_topic_accuracy --update-baseline"
        )

    assert agg["top1"] >= baseline["top1"] - TOLERANCE, (
        f"top1 regressed: {agg['top1']:.4f} < baseline {baseline['top1']:.4f} "
        f"(tol {TOLERANCE})"
    )
    assert agg["top3_hit_rate"] >= baseline["top3_hit_rate"] - TOLERANCE, (
        f"top3_hit_rate regressed: {agg['top3_hit_rate']:.4f} < "
        f"baseline {baseline['top3_hit_rate']:.4f} (tol {TOLERANCE})"
    )
    assert agg["mrr"] >= baseline["mrr"] - TOLERANCE, (
        f"mrr regressed: {agg['mrr']:.4f} < baseline {baseline['mrr']:.4f} "
        f"(tol {TOLERANCE})"
    )


if __name__ == "__main__":
    update = "--update-baseline" in sys.argv
    results, agg = run_all()
    for r in results:
        print(
            f"{r.id:20}  top1={r.top1}  top3={r.top3_hit_rate:.2f}  "
            f"mrr={r.mrr:.2f}  actual_top_3={r.actual_top_3}"
        )
    print(
        f"\nAggregate: top1={agg['top1']:.4f}  "
        f"top3_hit_rate={agg['top3_hit_rate']:.4f}  mrr={agg['mrr']:.4f}"
    )
    if update:
        BASELINE_PATH.write_text(json.dumps(agg, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote baseline to {BASELINE_PATH}")
