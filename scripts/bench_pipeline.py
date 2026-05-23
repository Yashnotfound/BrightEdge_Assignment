"""Benchmark per-stage pipeline latency across HTML fixtures.

Run from repo root:
    .local/venv/bin/python scripts/bench_pipeline.py --iters 20

Reports per-stage p50 / p95 / mean across all (fixture x iteration) samples,
plus a per-fixture summary. Operates on the in-memory pipeline only — no
network, no S3, no DynamoDB.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "tests" / "fixtures"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100 * (len(s) - 1)))))
    return s[k]


def _bench(iters: int, fixtures: list[Path]) -> dict[str, dict[str, list[float]]]:
    """Return {fixture_name: {stage: [duration_ms]}}."""
    from crawler.pipeline import process_html_timed  # lazy: respect optional install

    out: dict[str, dict[str, list[float]]] = {}
    for path in fixtures:
        html = path.read_text(encoding="utf-8")
        per_stage: dict[str, list[float]] = {}
        # one warmup iteration to populate YAKE / langdetect caches
        process_html_timed(
            url=f"http://example.com/{path.stem}", html=html, http_status=200,
            content_type="text/html", fetcher_used="static",
        )
        for _ in range(iters):
            _, timings = process_html_timed(
                url=f"http://example.com/{path.stem}", html=html, http_status=200,
                content_type="text/html", fetcher_used="static",
            )
            for stage, dur in timings.items():
                per_stage.setdefault(stage, []).append(dur)
        out[path.stem] = per_stage
    return out


def _print_per_fixture(results: dict[str, dict[str, list[float]]]) -> None:
    stages = sorted({s for v in results.values() for s in v})
    print(f"\n{'Fixture':35} " + " ".join(f"{s:>10}" for s in stages))
    print("-" * (35 + 11 * len(stages)))
    for name in sorted(results):
        per_stage = results[name]
        row = [f"{name:35}"]
        for stage in stages:
            samples = per_stage.get(stage, [])
            row.append(f"{statistics.median(samples):>10.2f}" if samples else f"{'-':>10}")
        print(" ".join(row))


def _print_aggregate(results: dict[str, dict[str, list[float]]]) -> None:
    # Pool across fixtures: one sample per (fixture, iteration) per stage.
    pooled: dict[str, list[float]] = {}
    for per_stage in results.values():
        for stage, samples in per_stage.items():
            pooled.setdefault(stage, []).extend(samples)

    print("\nAggregate per stage (ms across all fixtures × iterations):")
    print(f"{'Stage':15} {'n':>6} {'p50':>10} {'p95':>10} {'mean':>10}")
    print("-" * 55)
    for stage in sorted(pooled):
        s = pooled[stage]
        print(
            f"{stage:15} {len(s):>6} "
            f"{_percentile(s, 50):>10.2f} {_percentile(s, 95):>10.2f} "
            f"{statistics.mean(s):>10.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iters", type=int, default=10, help="iterations per fixture")
    parser.add_argument(
        "--fixtures", type=Path, default=FIXTURES_DIR,
        help="directory containing *.html fixtures",
    )
    parser.add_argument(
        "--glob", default="*.html",
        help="fixture filename glob (default: *.html)",
    )
    args = parser.parse_args()

    fixtures = sorted(args.fixtures.glob(args.glob))
    if not fixtures:
        print(f"No fixtures matching {args.glob} in {args.fixtures}", file=sys.stderr)
        return 1
    print(f"Benching {len(fixtures)} fixtures × {args.iters} iterations + 1 warmup.")
    results = _bench(args.iters, fixtures)
    _print_per_fixture(results)
    _print_aggregate(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
