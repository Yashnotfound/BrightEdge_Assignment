# BrightEdge Scale Take-Home — Design Spec

**Date:** 2026-05-23
**Author:** Candidate (with Claude Code assistance)
**Assignment:** BrightEdge Engineering Developer Candidate Assignment — Scale
**Deadline:** 48 hours from receipt of instructions

---

## 1. Context

BrightEdge's take-home asks for a URL-to-topics service that must scale to billions of URLs ingested per month with millions of read requests, optimized for cost, performance, and availability. The submission has three parts:

1. **Code:** a deployed crawler that extracts metadata and topics from any URL.
2. **Design doc:** how to operationalize this for billions of URLs, including unified schema, SLOs/SLAs, and monitoring.
3. **PoC plan:** engineering breakdown, blockers, estimates, and release strategy.

Test URLs the live demo must handle:
- `http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/…` (heavy anti-bot)
- `http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/` (static blog)
- `https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai` (hydrated news)

Allowed: 3rd-party libraries, AI assistance (must disclose), AWS/GCP/Azure managed services.
Disallowed: 3rd-party services that replace the crawler itself (e.g., Diffbot, ScrapingBee).

## 2. Goals

- **Functional:** given any URL, return structured metadata (title, description, OG, JSON-LD, body, language) and a ranked list of topics with scores.
- **Scale story:** architecture must extrapolate cleanly from the demo to 5B URLs/month at ~$6 per 1k URLs.
- **Operational story:** unified schema, defined SLOs, monitoring with clear cost levers.
- **PoC story:** an honest, dated phase plan from take-home to GA with risk-categorized blockers.

## 3. Non-goals

- Multi-tenant authn/authz at PoC scope (called out as Phase 1).
- Production-grade anti-bot countermeasures beyond a confidence-driven headless fallback.
- A full classifier eval harness with labeled ground truth (called out as Phase 3).
- Non-English content handling beyond `langdetect` tagging.

## 4. Constraints

- **Time:** 48 hours wall-clock from instruction receipt.
- **Single contributor** with AI assistance.
- **Public demo URL** must be reachable for reviewers.
- **AI tools must be disclosed** in the submission.

## 5. Decisions summary

| Dimension | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Best ecosystem for crawl/parse/NLP; fastest path to working demo |
| Web framework | FastAPI + Mangum | Auto-generated `/docs`, type-safe via Pydantic, Lambda-ready |
| Fetcher | `httpx` static-first, Playwright headless fallback | Cost-aware: escalate only when confidence is low |
| Body extraction | `trafilatura` | Best-in-class boilerplate stripping for news/blog/product pages |
| Classifier | Hybrid: heuristics (meta/OG/JSON-LD) + YAKE keyphrases + optional KeyBERT semantic rerank | No per-call API cost; defensible cost story at billions of URLs |
| Cloud | AWS | Strongest service catalog for the scale design; reviewer expectation |
| Deployment | All-Lambda (API + static-worker + headless-worker container image) | Scales to zero, cleanest cost narrative |
| IaC | AWS SAM | Lowest friction for Lambda container images + API GW |
| Storage | S3 (raw HTML, Parquet, jobs) + DynamoDB (Pages, Frontier, Jobs) + ElastiCache (read cache) | Cost-tiered, supports hot reads and cold analytics |
| Queueing | SQS, sharded per top-50 domain + shared long-tail queue | Built-in politeness via per-queue concurrency |
| Demo UX | Minimal HTML form + FastAPI `/docs` Swagger UI | Best polish-to-effort ratio |
| Hosting | Public GitHub repo + AWS demo URL | Standard professional surface; .txt-zip email fallback |

## 6. Part 1 — Crawler service

### 6.1 Module layout (`crawler/`)

```
crawler/
├── api/
│   ├── main.py           # Mangum-wrapped FastAPI ASGI handler
│   ├── routes.py         # /extract, /batch, /jobs/{id}, /pages/{url_hash}
│   └── schemas.py        # Pydantic request/response models
├── fetcher/
│   ├── static.py         # httpx + realistic UA + retries + redirects + robots cache
│   ├── headless.py       # Playwright launcher (separate Lambda)
│   └── confidence.py     # 0.0–1.0 score, decides if headless escalation is needed
├── extractor/
│   ├── meta.py           # <title>, <meta>, OpenGraph, Twitter Card, canonical
│   ├── jsonld.py         # JSON-LD / schema.org / microdata
│   ├── body.py           # trafilatura main-text extraction
│   └── language.py       # langdetect
├── classifier/
│   ├── heuristics.py     # meta-keywords, OG, schema categories → weighted candidates
│   ├── keyphrases.py     # YAKE over body
│   ├── embeddings.py     # KeyBERT semantic rerank (behind flag)
│   └── fuse.py           # merge + dedupe by stem + score + top-K
├── storage/
│   ├── s3.py             # raw HTML put, manifest writes
│   └── dynamo.py         # Pages, Frontier, Jobs accessors
├── workers/
│   ├── static_worker.py  # SQS handler → static fetch → extract → classify → persist
│   └── headless_worker.py# triggered for low-confidence pages
└── infra/
    └── template.yaml     # SAM: 3 Lambdas, API GW, SQS, DDB, S3, IAM
```

### 6.2 API contract

| Endpoint | Method | Behavior |
|---|---|---|
| `/extract` | POST | Body `{url}`. Synchronous: fetch → extract → classify → respond. 25s timeout. |
| `/batch` | POST | Body `{urls: [...]}` (max 1000). Returns `{job_id}`. Fans out SQS messages. |
| `/jobs/{id}` | GET | Returns `{status, total, succeeded, failed, manifest_s3_uri?}`. |
| `/pages/{url_hash}` | GET | Returns last cached extraction. |
| `/docs` | GET | FastAPI Swagger UI. |
| `/` | GET | Minimal HTML form for reviewers. |

### 6.3 Classification pipeline

Three signal layers fused by `classifier/fuse.py`:

1. **Heuristic (high precision):** meta-keywords, `og:*`, `article:tag`, JSON-LD `keywords`/`category`, `<title>` n-grams, `<h1>` n-grams. Weighted: schema.org category = 2.0, meta keyword = 1.5, title n-gram = 1.0.
2. **Keyphrase (high recall):** YAKE over the trafilatura-extracted body → top-20 candidates with YAKE scores (inverted: lower YAKE = higher topic weight).
3. **Semantic rerank (optional, flag `--with-embeddings`):** KeyBERT with `all-MiniLM-L6-v2` for cosine-sim folding of near-duplicates. Default OFF on the API Lambda; ON in the headless worker (model already warm there).

Output: ranked `topics: [{label, score, sources: [...]}]` top-10.

### 6.4 Confidence & headless escalation

`confidence.py` returns a float on `[0,1]`:

- title present: +0.3
- body word count buckets: up to +0.4
- structured data found: +0.2
- non-CAPTCHA fingerprint: +0.1

Threshold `< 0.5` triggers headless retry. Per-domain overrides supported.

### 6.5 Result schema (Pydantic)

```json
{
  "url": "...", "url_hash": "<sha256>",
  "fetched_at": "ISO-8601", "fetcher_used": "static|headless",
  "http_status": 200, "content_type": "text/html", "language": "en",
  "title": "...", "description": "...", "canonical_url": "...",
  "open_graph": {...}, "twitter_card": {...}, "json_ld": [...],
  "body_text": "(<=50KB)", "word_count": 1234,
  "topics": [{"label": "toasters", "score": 0.82,
              "sources": ["og:product:category", "title"]}, ...],
  "extraction_confidence": 0.91,
  "errors": []
}
```

## 7. Part 2 — Scale design

### 7.1 Sizing assumptions

| Quantity | Assumption | Value |
|---|---|---|
| URLs per month | "billions" | 5B |
| Sustained crawl rate | 5B / 30d | ~1,930 URL/s |
| Read QPS on metadata | "millions of requests" | ~100 RPS sustained, 1k peak |
| Avg HTML size | typical | ~200 KB raw, ~60 KB gzipped |
| Raw HTML/month | 5B × 60 KB | ~300 TB |
| Extracted metadata/URL | post-processing | ~2 KB → 10 TB/month |

### 7.2 Reference architecture

```
                INGEST                              CRAWL                                 SERVE
   ┌──────────────────────────┐      ┌───────────────────────────────┐      ┌───────────────────────────────┐
   │ S3 dropzone (urls.txt)   │      │  Per-domain SQS shards        │      │ Read API (Lambda)             │
   │ MySQL inbox (DMS → KDS)  │──┐   │  + per-domain token bucket    │      │     │                         │
   └──────────────────────────┘  │   │  (DDB) for politeness         │      │     ▼                         │
                                 │   └───────────┬───────────────────┘      │ ElastiCache (read-through)    │
                                 ▼               │                          │     │  miss                    │
                          Ingest Lambda          ▼                          │     ▼                         │
                          (normalize,     Static Worker Lambda (fleet)      │ DynamoDB Pages (hot)          │
                          dedupe vs            │ low conf?                  │                                │
                          Frontier,            ▼                            │ Athena over Parquet (cold)    │
                          fan to SQS)   Headless Worker Lambda              │ (analyst queries)              │
                                 │      (container w/ chromium)             │                                │
                                 ▼                  │                       └───────────────────────────────┘
                          Frontier DDB              ▼
                          (URL hash,         ┌──────────────────────┐
                          status, attempts,  │ Persist:             │
                          dedupe, TTL)       │ • S3 raw HTML (zstd) │
                                             │ • DDB Pages          │
                                             │ • Glue/Parquet (ETL) │
                                             │ • DLQ on failure     │
                                             └──────────────────────┘
```

### 7.3 Unified data schema

**DynamoDB `Pages`** — hot lookups by URL.

```
PK url_hash        SK version    url, domain, fetched_at, http_status,
                                 fetcher_used, language, title, description,
                                 canonical_url, og {…}, jsonld_present (bool),
                                 topics [{label, score, sources}],
                                 extraction_confidence, s3_html_uri,
                                 schema_version
GSI1  domain       fetched_at    (range scans per domain)
GSI2  topic_label  score         (top pages per topic — sparse)
```

**DynamoDB `Frontier`** — URL queue & dedupe.

```
PK url_hash    url, domain, status (queued|in_flight|done|failed),
               attempts, next_retry_after, batch_id, source,
               first_seen, last_crawled, etag, last_modified
              TTL on done entries (90d)
```

**DynamoDB `Jobs`** — batch tracking.

```
PK job_id    created_at, status, total, succeeded, failed, manifest_s3_uri
```

**S3 lake layout** (Parquet mirrors DDB Pages for analytics parity):

```
s3://crawler-prod/
  raw/domain={d}/year={y}/month={m}/day={d}/{url_hash}.html.zst
  parquet/pages/year={y}/month={m}/day={d}/part-{n}.parquet  ← Glue Catalog → Athena
  jobs/{job_id}/{manifest.json, results.jsonl.zst}
  dropzone/{tenant}/{year}/{month}/urls.txt
```

**Lifecycle:** raw HTML → IA at 30d → Glacier Deep Archive at 90d ($0.00099/GB). Parquet stays in Standard. DDB Pages retain latest version forever; older versions migrate to Parquet only.

### 7.4 Ingest, politeness, escalation

- **Ingest Lambda** is S3-event-triggered; streams the URL list, normalizes (lowercase host, strip fragments, sort query params), hashes, upserts into Frontier with `status=queued` only for new hashes. Existing hashes bump `last_seen` only.
- **Per-domain SQS shards** (one per top-50 domain, one shared long-tail). Static worker concurrency capped per queue → built-in politeness.
- **Token bucket in DDB** per domain enforces `robots.txt` crawl-delay. Worker reads/decrements atomically before fetching. `robots.txt` cache TTL 24h.
- **Headless escalation** only when confidence < 0.5. Steady-state target **< 10% escalation rate** — monitored as the #1 cost driver.
- **Domain reputation:** 5 consecutive 4xx/CAPTCHA → frontier entries for that domain go to a 24h cool-off bucket.
- **Incremental re-crawl:** HEAD with `If-None-Match`/`If-Modified-Since` from prior ETag in Frontier; 304 → skip body fetch entirely.

### 7.5 Read path

- `GET /pages?url=…` → API Lambda → ElastiCache Redis (`SETEX` 1h on miss) → DDB Pages by `url_hash`.
- `GET /pages/by-topic/{label}` → GSI2 query, paginated.
- `POST /search` → Athena query against Parquet for analyst joins. Async: returns `query_id`, client polls.

### 7.6 SLOs & SLAs

**SLOs (internal targets):**

| Indicator | SLO |
|---|---|
| `/extract` sync p99 latency | static < 3s; headless < 25s |
| `/batch` enqueue p99 (1k URLs) | < 2s |
| Read API `/pages/*` p99 | < 100 ms |
| Read API availability | 99.95% / 30d |
| Crawl pipeline availability | 99.9% / 30d |
| Batch completion (1M URLs) | < 4 hours, p95 |
| Extraction success (per-domain) | > 90% high-confidence |
| Headless escalation rate | < 10% steady state |

**SLAs (customer-facing):**

- Read API: 99.5% monthly uptime with service credits.
- Ingest: best-effort throughput; no hard completion SLA (domain rate-limits dominate).
- Data freshness: configurable per-tenant; default re-crawl every 30 days.
- Retention: raw HTML 90d hot + Glacier 2 years; metadata indefinite.

### 7.7 Monitoring

**Golden signals per service:** RPS, p50/p99 latency, error rate, saturation.

**Domain-specific custom metrics:**

- `extraction.confidence` (histogram)
- `headless.escalation_rate` (gauge per domain) — #1 cost lever
- `captcha.detected_rate` (per domain)
- `frontier.depth_by_domain` (gauge)
- `crawl.success_rate` (rolling 1h, per domain)
- `cost.per_1k_urls` (computed daily from Lambda + DDB CW metrics)
- `dlq.message_count` (alarm > 0)
- `read_api.cache_hit_rate` (target > 80%)

**Dashboards:** "Ops" (queue depth, error rates, DLQ); "Quality" (confidence histo, topic-count histo, schema-validity); "Cost" (per-1k-URL trend, escalation rate, S3 storage class breakdown).

**Alarms:** DLQ > 0; escalation rate > 20% sustained 15min; per-domain error rate > 10% in 5min; read API p99 > 200ms.

**Tracing:** AWS X-Ray on the static → headless escalation path.

**Logs:** structured JSON to CloudWatch Logs; CloudWatch Logs Insights for ad-hoc queries.

### 7.8 Cost model @ 5B URL/month

| Component | Monthly |
|---|---|
| Lambda — static workers (~90% traffic) | ~$3K |
| Lambda — headless workers (~10%) | ~$15K |
| DynamoDB Pages writes + reads | ~$8K |
| SQS | ~$0.2K |
| S3 raw (with Glacier lifecycle) | ~$2K amortized |
| S3 Parquet + Athena scans | ~$1K |
| ElastiCache (cache.r6g.large × 2) | ~$0.5K |
| CloudWatch + X-Ray | ~$0.5K |
| **Total** | **~$30K/mo ≈ $0.006/URL ≈ $6 per 1k URLs** |

**Cost levers (ranked by impact):**

1. Keep headless escalation < 10%.
2. ETag/304 short-circuit on re-crawls.
3. zstd over gzip on raw HTML (-30% storage).
4. Lambda Savings Plans.
5. DDB on-demand → provisioned with auto-scaling once volume is predictable.

## 8. Part 3 — PoC plan

### 8.1 Phased roadmap

| Phase | Duration | Definition of Done |
|---|---|---|
| **0 — Take-home PoC** | 48h | Live demo with sync + async paths; design docs for Parts 2 & 3; works on the 3 test URLs |
| **1 — Hardening** | ~2 weeks | Production observability; per-domain politeness; anti-bot v1; CI/CD; runbooks |
| **2 — Scale validation** | ~2 weeks | 1M URL/day load test passes; multi-tenant isolation; read API + cache; cost monitoring with budget alarms |
| **3 — Quality** | ~2 weeks | Classifier eval harness with labeled set; schema evolution policy; A/B framework for classifier variants |
| **4 — GA** | ~2 weeks | Top-50 domain tuning; 1B URL/month drill green; SLAs published; on-call rotation live |

### 8.2 Blocker triage

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

### 8.3 Implementation schedule

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

### 8.4 Release plan

1. **Feature-flag everything new** (LaunchDarkly or DDB-backed flags): `headless_fallback`, `keybert_enabled`, `read_cache_enabled`, `etag_short_circuit`.
2. **Shadow mode first** — for every new classifier variant, run new and old in parallel, log both, score offline. No user-facing impact until accuracy confirmed.
3. **Canary ramp:** 1% → 10% → 50% → 100% over 5 working days, with auto-rollback on SLO breach.
4. **Synthetic monitors:** canary hits `/extract` against fixed "golden" URLs every 5 min; alarms on classification drift, latency regression, or schema mismatch.
5. **Cost circuit breaker:** if `cost.per_1k_urls` exceeds budget by 25% for 1h, ingest auto-pauses and pages on-call.
6. **Game days:** before GA, run a chaos drill that kills the headless worker fleet and confirms graceful degradation (static-only with reduced confidence, not 5xx).

### 8.5 PoC evaluation (for the take-home itself)

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
- Estimated `$/1k URLs` on demo within 2× of Part 2 projection (< $12/1k at PoC scale).

### 8.6 Quality gates for the production team (post-PoC)

- Unit-test coverage > 80% on `extractor/` and `classifier/`.
- Contract tests against the OpenAPI spec.
- Smoke deploy + synthetic monitor green for 30 min.
- Cost dashboard reflects expected pattern.
- Each Lambda has a runbook entry (cause → symptom → mitigation).

## 9. 48-hour execution plan

### 9.1 Time budget

| Bucket | Hours |
|---|---|
| Part 1 code (write + test) | ~16h |
| AWS deploy + IaC | ~4h |
| Part 2 doc | ~4h |
| Part 3 doc | ~3h |
| README + AI disclosure + recording | ~2h |
| Buffer | ~3h |

### 9.2 Day-by-day plan with hard checkpoints

```
DAY 1 ── BUILD ──────────────────────────────────────────────────
H 0– 1   Repo scaffold, FastAPI skeleton, Pydantic models
H 1– 2   "Hello world" Lambda + API GW deployed         ← RISK GATE 1
H 2– 4   Static fetcher (httpx + UA + retries + robots cache)
H 4– 6   Extractor (meta, OG, JSON-LD, trafilatura, langdetect)
H 6– 8   Classifier (heuristics + YAKE), top-K fusion
H 8– 9   Wire end-to-end locally; test on 3 URLs
H 9–11   Deploy sync /extract + /docs + minimal HTML form
H11–12   Verify demo URL works for all 3 URLs
                                                         ◄ CHECKPOINT 1
                                                          Sync /extract LIVE

H12–14   DDB tables, S3 buckets, IAM
H14–16   /extract writes to DDB+S3; /pages/{url_hash} cached lookup
H16–19   SQS + static-worker Lambda; /batch + /jobs/{id}
H19–22   Headless worker (Playwright Lambda container)
                                                         ◄ RISK GATE 2
H22–24   End-to-end 50-URL batch test; smoke tests
                                                         ◄ CHECKPOINT 2
                                                          Full async path WORKING

(SLEEP)

DAY 2 ── DOCUMENT + POLISH ──────────────────────────────────────
H24–28   Part 2 design doc
H28–31   Part 3 PoC doc
H31–33   README (architecture, curl examples, AI disclosure, local dev)
H33–35   3-min screen recording
H35–38   Buffer: per-domain tuning (Amazon CAPTCHA), classifier tweaks
H38–41   Self-review pass: rerun all 3 curl examples; screenshots
H41–44   Final buffer
H44–48   Package & submit
```

### 9.3 Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| AWS account / IAM blocks deploy | Medium | "Hello world" Lambda in first 2h validates plumbing |
| Playwright Lambda container fails | Medium-High | Ship static-only if needed; flag as Phase 1 |
| Amazon CAPTCHA breaks demo | High | Headless handles most; `?fixture=1` toggle with visible "FIXTURE" banner as honest fallback |
| CNN/REI thin extraction | Low-Medium | trafilatura handles these in practice; verify by H8 |
| Doc time pressure | Medium | This spec IS the draft; Day 2 is mostly editing |
| Cold start kills demo UX | Low (sync), Medium (headless) | Provisioned concurrency 1 on API Lambda; document as knob |
| Classifier returns garbage | Medium | YAKE+heuristics has sane floor; tune on 3 URLs explicitly H6–9 |
| Submission file format gotcha | Low | GitHub primary, .txt-renamed zip fallback |

### 9.4 Minimum viable submission (worst-case)

If H22 risk gate is missed badly:
- **Part 1:** sync `/extract` only, no async/headless. Works on REI and CNN, documents Amazon CAPTCHA limitation with `?fixture=1` mode.
- **Part 2:** full design doc (this spec — won't slip).
- **Part 3:** full PoC doc (likewise).
- **README:** explicit about shipped vs Phase 1 work.

Still hits all evaluation criteria because Parts 2 and 3 carry the design-thinking signal.

### 9.5 Submission checklist (final 30 minutes)

- [ ] Demo URL responds for all 3 test URLs (or fixture-mode disclosure visible)
- [ ] `/docs` Swagger UI loads
- [ ] Repo has clean `README.md` at root
- [ ] AI tools section in README
- [ ] Architecture diagram (Mermaid in README, renders on GitHub)
- [ ] Cost table in Part 2 doc has real numbers, not placeholders
- [ ] Code files renamed to `.txt` if email submission is the channel
- [ ] Submission email includes: demo URL, repo URL, recording link
- [ ] Reply email confirming "received instructions" sent at start

## 10. Open questions / future work

- Multi-region failover strategy for the read API (single-region for PoC).
- GDPR/CCPA cache-eviction policy (legal review gates GA).
- Per-tenant rate limits and quota enforcement (Phase 1).
- Topic ontology: should we normalize to a fixed taxonomy (e.g., IAB Tech Lab categories) or stay free-form? Free-form for PoC; pluggable normalizer post-Phase 3.

## 11. AI tools disclosure

The submission's README will name:

- **Claude Code (Opus 4.7)** — used for design brainstorming, this spec, the implementation plan, code scaffolding, and documentation.
- Any other tools added during execution will be appended with a one-line "used for X" note.
