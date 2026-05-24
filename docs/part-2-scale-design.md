# Part 2 — Operationalizing the Crawler for Billions of URLs

> Companion to the BrightEdge take-home submission. This document describes
> how the deployed crawler (Part 1) extends to ingest billions of URLs/month
> with millions of read requests, optimized for cost, performance, and availability.

## 1. Sizing assumptions

| Quantity | Assumption | Value |
|---|---|---|
| URLs per month | "billions" | 5B |
| Sustained crawl rate | 5B / 30d | ~1,930 URL/s |
| Read QPS on metadata | "millions of requests" — interpret as 50M/day | ~500 RPS sustained, ~5k peak |
| Avg HTML size | typical | ~200 KB raw, ~60 KB gzipped (zstd ~40 KB) |
| Raw HTML/month | 5B × 50 KB (zstd-compressed steady state) | ~250 TB/month new data |
| Extracted metadata/URL | post-processing | ~2 KB → 10 TB/month |

## 2. Reference architecture

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

## 3. Unified data schema

**DynamoDB `Pages`** — hot lookups by URL.

```
PK url_hash        SK version    url, domain, fetched_at, http_status,
                                 fetcher_used, language, title, description,
                                 canonical_url, og {…}, jsonld_present (bool),
                                 topics [{label, score, sources}],
                                 extraction_confidence, s3_html_uri,
                                 s3_jsonld_uri, schema_version
GSI1  domain       fetched_at    (range scans per domain)
GSI2  topic_label  score         (top pages per topic — sparse)
```

**Field split (DDB vs S3):** DDB items are kept small (<8 KB) to stay efficient. Heavy fields live in S3:

- DDB stores: small fixed metadata (title, description, OG, topics, confidence), pointers (`s3_html_uri`, `s3_jsonld_uri`), and a `jsonld_present` boolean for cheap "does this page have structured data?" filtering.
- S3 stores: raw HTML (`{url_hash}.html.zst`) and the parsed JSON-LD blob (`{url_hash}.jsonld.json`). The `/extract` API response inlines JSON-LD by fetching it from S3 on read; the `/pages` cached lookup also inlines it.

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

### 3.1 Current classifier accuracy

The PoC ships with an eval harness
(`tests/eval/test_topic_accuracy.py`) that runs the full pipeline against
19 labeled HTML fixtures (`tests/eval/fixtures.yaml`) and compares against
a checked-in baseline (`tests/eval/baseline.json`). The harness is part of
the pytest suite and fails CI on any > 0.05 absolute regression.

| Metric | Value | What it measures |
|---|---|---|
| top-1 hit rate | 94.7% | Top-ranked topic matches an expected label |
| top-3 hit rate | 59.7% | Avg coverage of expected labels in top 3 |
| MRR | 97.4% | Mean reciprocal rank of first relevant topic |
| n | 19 | Amazon, REI, CNN, Wikipedia en/es/de/fr, GitHub, MDN, StackOverflow, arXiv, recipes |

Expanding the labeled set (and adding per-domain accuracy slices) is a
Phase 3 (Quality) deliverable in [Part 3](part-3-poc-plan.md) — see the
"Classifier eval harness" line in §2 and the Phase 3 row in §1.

## 4. Ingest, politeness, escalation

- **Ingest Lambda** is S3-event-triggered; streams the URL list, normalizes (lowercase host, strip fragments, sort query params), hashes, upserts into Frontier with `status=queued` only for new hashes. Existing hashes bump `last_seen` only.
- **Per-domain SQS shards** (one per top-50 domain, one shared long-tail). Static worker concurrency capped per queue → built-in politeness.
- **Token bucket in DDB** per domain enforces `robots.txt` crawl-delay. Worker reads/decrements atomically before fetching. `robots.txt` cache TTL 24h.
- **Headless escalation** only when confidence < 0.5. Steady-state target **< 10% escalation rate** — monitored as the #1 cost driver.
- **Domain reputation:** 5 consecutive 4xx/CAPTCHA → frontier entries for that domain go to a 24h cool-off bucket.
- **Incremental re-crawl:** HEAD with `If-None-Match`/`If-Modified-Since` from prior ETag in Frontier; 304 → skip body fetch entirely.

## 5. Read path

- `GET /pages?url=…` → API Lambda → ElastiCache Redis (`SETEX` 1h on miss) → DDB Pages by `url_hash`.
- `GET /pages/by-topic/{label}` → GSI2 query, paginated.
- `POST /search` → Athena query against Parquet for analyst joins. Async: returns `query_id`, client polls.

## 6. SLOs & SLAs

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

## 7. Monitoring

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

## 8. Cost model @ 5B URL/month

**Assumptions:** static worker 500ms avg @ 512 MB; headless worker 2s avg @ 2 GB; DDB items 2 KB; 10% headless escalation; on-demand DDB; 90-day hot retention + 2-year Glacier Deep Archive; us-east-1 pricing as of 2026-05.

**Unoptimized (lift-and-shift of the PoC topology to scale):**

| Component | Calc | Monthly |
|---|---|---|
| Lambda — static (4.5B × 0.5s × 0.5 GB) | 1.13B GB-s × $0.0000167 + 4.5B × $0.20/M req | ~$19K + ~$0.9K = **~$20K** |
| Lambda — headless (500M × 2s × 2 GB) | 2B GB-s × $0.0000167 + 500M × $0.20/M req | ~$33K + ~$0.1K = **~$33K** |
| DynamoDB writes (5B × 2 WRU) | 10B WRU × $1.25/M | **~$13K** |
| DynamoDB reads (1.3B @ 4 KB strongly consistent) | ~325M RCU × $0.25/M | **~$0.1K** |
| SQS (5B sends + 5B receives + DLQ) | 10B × $0.40/M | **~$4K** |
| S3 raw — steady state with 90d hot / 2yr Glacier | ~300 TB hot (Standard+IA blend) + ~7,200 TB Glacier Deep Archive | **~$22K** |
| S3 Parquet + Athena scans | ~10 TB scanned/mo × $5/TB | **~$1K** |
| ElastiCache (cache.r6g.large × 2, multi-AZ) | $0.252/hr × 2 × 730 | **~$0.5K** |
| CloudWatch metrics + Logs + X-Ray | typical at this volume | **~$1K** |
| Data transfer (assume 5% egress to /pages clients) | rough | **~$0.5K** |
| **Total (unoptimized)** | | **~$95K/mo ≈ $19 per million URLs** |

**Optimized (Phase 2/3 levers applied):**

| Lever | Saving | Result |
|---|---|---|
| Headless tier migration off Lambda to Fargate w/ high concurrency (Phase 2) | Headless $33K → $14K | -$19K |
| Reduce escalation rate from 10% → 5% via better static heuristics | Headless $14K → $7K | -$7K |
| Static tier partial migration to Fargate at sustained throughput (Phase 4) | Static $20K → $14K (the steady portion; spike traffic stays on Lambda) | -$6K |
| Lambda Savings Plans on remaining Lambda compute (1-yr, ~17% off) | -$1K | -$1K |
| DDB provisioned with auto-scaling once volume is predictable | DDB $13K → $8K | -$5K |
| ETag/304 short-circuit on re-crawls (40% of traffic is re-crawl) | Static + DDB writes -40% on repeat traffic | -$10K |
| zstd over gzip for raw HTML, plus more aggressive lifecycle to Glacier | S3 raw $22K → $15K | -$7K |
| **Total (optimized)** | | **~$60K/mo ≈ $12 per million URLs** |

**Unit-rate translations (so the cost claim is unambiguous):**

| Scale | Unoptimized | Optimized |
|---|---|---|
| Per URL | $0.000019 | $0.000012 |
| Per 1,000 URLs | $0.019 | $0.012 |
| Per 1,000,000 URLs | **$19** | **$12** |
| Per 1,000,000,000 URLs | $19,000 | $12,000 |
| 5B URLs/month total | $95K/mo | $60K/mo |

**Cost levers (ranked by impact, see table above for $):**

1. **Headless tier migration to Fargate** at sustained volume — biggest single win.
2. **S3 storage tiering** (zstd + aggressive Glacier lifecycle) — second biggest.
3. **ETag/304 short-circuit** on re-crawls — huge on repeat passes.
4. **Keep escalation rate < 10%** (target 5% once domain-specific tuning lands).
5. **DDB on-demand → provisioned** once volume is predictable.
6. **Static tier migration to Fargate** for the predictable steady-state portion of traffic (Phase 4); Lambda still absorbs spikes.

**Why Lambda for everything at PoC?** The demo runs all-Lambda because it scales to zero — free when idle, no minimum cost floor for a public demo URL. At sustained billion-URL volume the economics flip and Fargate (or even self-managed EKS) for the steady portion of static + headless workloads wins on $/URL by ~3x. The Phase 2/4 cost-optimization work is the bridge from the demo topology to the $12/M optimized target.
