# Part 3 — Proof of Concept Engineering Plan

> Companion to the BrightEdge take-home submission. This document breaks
> down how the deployed crawler PoC extends through hardening, scale validation,
> quality, and GA, with blocker triage, schedules, and release strategy.

## 1. Phased roadmap

| Phase | Duration | Definition of Done |
|---|---|---|
| **0 — Take-home PoC** | ~33h wall-clock (2026-05-23 11:50 → 2026-05-24 20:41 IST, 83 commits) | Live demo with sync + async paths; design docs for Parts 2 & 3; works on the 3 test URLs |
| **1 — Hardening** | ~2 weeks | Production observability; per-domain politeness; anti-bot v1; CI/CD; runbooks |
| **2 — Scale validation** | ~2 weeks | 1M URL/day load test passes; multi-tenant isolation; read API + cache; cost monitoring with budget alarms |
| **3 — Quality** | ~2 weeks | Classifier eval harness with labeled set; schema evolution policy; A/B framework for classifier variants |
| **4 — GA** | ~2 weeks | Top-50 domain tuning; 1B URL/month drill green; SLAs published; on-call rotation live |

## 1a. Phase 0 actuals — real approach and what shipped

### Real approach

The PoC was built solo by Yash with **Claude Code (Opus 4.7)** assistance over **~33h wall-clock** spread across 2026-05-23 and 2026-05-24 (83 commits — see git log). Work proceeded in four sprints, with `intent-reviewer` + `objective-reviewer` subagents dispatched after each substantive change:

1. **Sprint A — Initial PoC** (2026-05-23 11:50 → 13:46, ~2h core build): design spec → implementation plan → end-to-end vertical slice (fetch → extract → classify → persist → API) → SAM infra → Part 2/3 docs → submission README. Full async path + headless container shipped in this window.
2. **Sprint B — Perf + quality fixes** (2026-05-23 15:59 → 19:59): HTTPX decompression bug, YAKE weight saturation fix, parse-HTML-once + concurrent storage writes, per-stage timing instrumentation. Phase 2 reviewer feedback addressed.
3. **Sprint C — Auth + Lambda compat** (2026-05-23 21:17 → May-24 12:08): Bearer-token auth on data endpoints, sparticuz/chromium for Lambda compat, macOS UF_HIDDEN venv setup script, deploy runbook + buildx image script, module-docs sync.
4. **Sprint D — Robustness pass** (2026-05-24 14:47 → 20:41): graceful fetch-failure handling (timeouts, firewalls, 4xx/5xx/captcha rejection at the persist gate), idempotent job counter via per-URL claim token, ghost-row guards, terminal-status correctness, classifier metrics, headless-escalation observability on `/extract`.

### What actually shipped (vs. design spec §6)

**Fully built and deployed:**
- FastAPI on Lambda via Mangum (API Gateway HTTP API)
- Static fetcher: httpx + realistic UA + retries + robots.txt cache + confidence scorer + CAPTCHA fingerprint
- Headless fallback: Playwright Lambda container with sparticuz/chromium; confidence-driven escalation (threshold 0.5)
- Extractor: title, meta, OG, Twitter Card, canonical, JSON-LD, trafilatura body, langdetect language
- Hybrid classifier: heuristics (meta/OG/JSON-LD weighted) + YAKE keyphrases + fused top-K topics
- Persist gate: rejects 4xx/5xx, 429, and CAPTCHA responses; persists degraded markers for audit
- Storage: DynamoDB `Pages` + `Jobs` repositories; S3 raw-HTML (gzip) + JSON-LD writers; URL normalization + SHA-256 hashing
- Async path: SQS queue + static-worker Lambda; `POST /batch` and `GET /jobs/{id}`; idempotent per-URL claim tokens
- Endpoints: `POST /extract`, `POST /batch`, `GET /jobs/{id}`, `GET /pages?url`, `GET /pages/{hash}`, `GET /docs` (Swagger UI), `GET /` (minimal HTML demo)
- API-key Bearer auth on data endpoints (Layer 2 defense)
- `?fixture=1` mode for Amazon CAPTCHA (visible "FIXTURE" banner — honest fallback)
- SAM IaC: API Lambda, static-worker Lambda, headless-worker Lambda (container image), API GW, SQS + DLQ, DDB tables, S3 buckets, IAM
- Deploy runbook (`docs/deploy.md`) + working `deploy.sh` + buildx image script
- Smoke script for end-to-end verification on all 3 test URLs
- Module-docs structure (`docs/modules/*.md`) with `PreToolUse` commit-validator + `Stop` doc-sync hooks
- `intent-reviewer` + `objective-reviewer` subagents wired into the workflow

**Deferred from the design-spec scope (deliberate):**
- KeyBERT semantic rerank — code path present behind `--with-embeddings` flag, default OFF on API Lambda (no warm-model assumption holds at PoC scale)
- Per-domain SQS shards + token-bucket politeness at fleet scale — single shared queue + in-process robots cache for PoC
- ElastiCache read-through cache — DDB direct reads for PoC (read QPS too low to justify the line item)
- Glue Catalog + Parquet ETL + Athena read path — not built; deferred to Phase 2 scale validation
- Multi-tenant authn/authz beyond a single Bearer token — Phase 1
- Production-grade anti-bot countermeasures beyond confidence escalation + fixture fallback — Phase 1 / Phase 2

**Beyond original DoD (added during sprints C+D):**
- Per-stage timing instrumentation + bench script (`scripts/bench.py`)
- Schema-drift guards on the storage layer
- Headless-escalation outcome tracked on `/extract` response for observability
- Empirical confidence floor at persist gate (handpicked 0.5, see [`docs/known-gaps.md`](known-gaps.md))
- `docs/known-gaps.md` — catalog of deferred fixes with code pointers and remediation directions

### Known gaps observed during the build

A 1000-URL post-deploy smoke test on 2026-05-24 (job `80db5fce86a7409b8930a347c1362007`) surfaced several issues that were either fixed in Sprint D or catalogued. The full list (with code pointers and proposed directions) lives in [`docs/known-gaps.md`](known-gaps.md). The single most impactful unresolved gap is that `/jobs/{id}` returns counters only — no per-URL result manifest — so the client must retain its submitted URL list to enumerate results.

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
- **Note on the $12/$19 per million URL figures referenced here and in Part 2 §8:** these are optimistic estimates that assume the < 10% escalation target holds, no anti-bot mitigation overhead, and steady-state throughput. Realistic production cost is likely 1.5–2× higher (this is a *production cost inflation* multiplier — separate from the "< 2× of optimized projection" PoC-accuracy tolerance in the bullet above, which is about whether the demo extrapolates correctly). See the disclaimer at the top of Part 2 §8 for the full list of caveats.

## 6. Quality gates for production team

- Unit-test coverage > 80% on `extractor/` and `classifier/`.
- Contract tests against the OpenAPI spec.
- Smoke deploy + synthetic monitor green for 30 min.
- Cost dashboard reflects expected pattern.
- Each Lambda has a runbook entry (cause → symptom → mitigation).
