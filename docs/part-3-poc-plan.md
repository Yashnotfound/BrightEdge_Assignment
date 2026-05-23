# Part 3 — Proof of Concept Engineering Plan

> Companion to the BrightEdge take-home submission. This document breaks
> down how the deployed crawler PoC extends through hardening, scale validation,
> quality, and GA, with blocker triage, schedules, and release strategy.

## 1. Phased roadmap

| Phase | Duration | Definition of Done |
|---|---|---|
| **0 — Take-home PoC** | 48h | Live demo with sync + async paths; design docs for Parts 2 & 3; works on the 3 test URLs |
| **1 — Hardening** | ~2 weeks | Production observability; per-domain politeness; anti-bot v1; CI/CD; runbooks |
| **2 — Scale validation** | ~2 weeks | 1M URL/day load test passes; multi-tenant isolation; read API + cache; cost monitoring with budget alarms |
| **3 — Quality** | ~2 weeks | Classifier eval harness with labeled set; schema evolution policy; A/B framework for classifier variants |
| **4 — GA** | ~2 weeks | Top-50 domain tuning; 1B URL/month drill green; SLAs published; on-call rotation live |

## 2. Blocker triage

**Known & trivial (engineering-only):**
- API GW + Lambda + Mangum wiring — 0.5d, low
- DDB schema — 1d, low
- SAM IaC for full topology — 2d, low
- Glue Catalog + Parquet ETL — 2d, low
- Read API + ElastiCache — 2d, low
- Athena query endpoint — 2d, low
- CI/CD (GitHub Actions → SAM deploy with PR previews) — 1d, low
- Standard CloudWatch dashboards & alarms — 2d, low

**Known & non-trivial (need design):**
- Headless Lambda container (chromium + Playwright, optimize cold start) — 3d, medium
- Per-domain politeness at fleet scale — 4d, medium
- Anti-bot detection v1 — 5d, **HIGH**
- Cold-start optimization (provisioned concurrency, SnapStart) — 3d, medium
- Classifier eval harness (needs ground-truth labels) — 8d, **HIGH**
- Schema evolution policy — 2d, medium
- DDB hot-key avoidance for high-volume domains — 2d, medium
- Cost guardrails (hourly $-spend alarm + circuit breaker) — 2d, medium

**Unknown / research-required:**
- Per-domain anti-bot signatures (Amazon, Walmart, BestBuy, etc.) — open-ended, **HIGH**
- Multi-tenant noisy-neighbor isolation — 4d, medium
- Legal review: large-scale crawling TOS, GDPR/CCPA — external, gates GA
- ML model drift over time — requires Phase 3 eval harness first
- Non-English content handling — open-ended

## 3. Implementation schedule

```
Week  1   2   3   4   5   6   7   8
─────────────────────────────────────────
P1 ████████
P2         ████████
P3                 ████████
P4                         ████████

Critical-path items per phase:
P1: anti-bot v1  ← gates load testing
P2: load test    ← gates GA scale claim
P3: eval harness ← gates classifier confidence claim in SLA
P4: top-50 tune  ← gates per-domain SLOs
```

Two parallel tracks:
- **Track A (platform):** ingestion, queues, workers, storage, API.
- **Track B (quality):** classifier, eval, ground-truth, domain tuning.

Track B can't fully validate until Track A's load test ships; Track B's hardest milestone (eval harness, week 6) intentionally trails Track A's scale validation.

## 4. Release plan

1. **Feature-flag everything new** (LaunchDarkly or DDB-backed flags): `headless_fallback`, `keybert_enabled`, `read_cache_enabled`, `etag_short_circuit`.
2. **Shadow mode first** — for every new classifier variant, run new and old in parallel, log both, score offline. No user-facing impact until accuracy confirmed.
3. **Canary ramp:** 1% → 10% → 50% → 100% over 5 working days, with auto-rollback on SLO breach.
4. **Synthetic monitors:** canary hits `/extract` against fixed "golden" URLs every 5 min; alarms on classification drift, latency regression, or schema mismatch.
5. **Cost circuit breaker:** if `cost.per_1k_urls` exceeds budget by 25% for 1h, ingest auto-pauses and pages on-call.
6. **Game days:** before GA, run a chaos drill that kills the headless worker fleet and confirms graceful degradation (static-only with reduced confidence, not 5xx).

## 5. PoC evaluation

**Functional:**
- `POST /extract` returns non-empty `topics` (len ≥ 3) with `confidence > 0.5` for all 3 test URLs.
- `POST /batch` of 10 URLs completes within 60s, writes manifest to S3.
- `GET /jobs/{id}` reflects partial progress and final state.

**Performance (PoC bar, not prod):**
- p95 `/extract` < 5s for static, < 20s for headless.
- 100 URL/min sustained over 5 min without errors.

**Quality (manual signal):**
- 3 reviewers blind-rate topic relevance (1–5) on 30 random URLs; mean ≥ 3.5.
- No CAPTCHA-page false positives on the 3 test URLs.

**Cost (sanity check):**
- Headless escalation rate on the 3 test URLs matches the < 10% design target (Amazon is expected to escalate; REI and CNN shouldn't).
- Demo per-URL cost is dominated by Lambda fixed overhead and won't match the production projection at low volume, but the **scaled-up extrapolation** (compute-seconds per URL × projected 5B URLs/mo) should land within 2× of the Part 2 optimized projection (~$12 per million URLs, so < ~$24 per million URLs extrapolated).

## 6. Quality gates for production team

- Unit-test coverage > 80% on `extractor/` and `classifier/`.
- Contract tests against the OpenAPI spec.
- Smoke deploy + synthetic monitor green for 30 min.
- Cost dashboard reflects expected pattern.
- Each Lambda has a runbook entry (cause → symptom → mitigation).
