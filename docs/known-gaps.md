# Known gaps and deferred fixes

Catalog of issues observed in code or production runs that were intentionally
deferred. Each entry names the gap, what it costs in practice, where in the
code/infra it lives, and the proposed direction.

Surfaced by either the 1000-URL post-deploy smoke test
(2026-05-24, job `80db5fce86a7409b8930a347c1362007`) or earlier reviews.

---

## API surface

### `/jobs/{id}` returns counters only, no per-URL results

The job endpoint reports `status / succeeded / failed / total`. To retrieve
the extracted data the client must remember the URL list it submitted and
call `GET /pages?url=…` per URL. Losing the input list means losing the
ability to enumerate the job's results.

- **Impact:** No "give me all results of this job" recovery path. Limits
  multi-tenant scenarios where the submitter and the consumer differ.
- **Code:** [src/crawler/api/routes.py:359-367](src/crawler/api/routes.py:359),
  [src/crawler/api/schemas.py:60](src/crawler/api/schemas.py:60) (`JobStatus.manifest_s3_uri`
  field exists but is never populated).
- **Direction:** On terminal job state, a finalizer writes
  `s3://crawler-prod/jobs/{job_id}/manifest.json` with the full URL list +
  per-URL outcome; populate `manifest_s3_uri`. Designed for in the scale
  doc — not built.

### Empirical confidence floor for 4xx persist-gate

`persist_gate.reject_reason` lets HTTP 401/402/403/451 responses through when
`extraction_confidence >= 0.5`. The 0.5 threshold is hand-picked from
observed data and is not derived from a labeled dataset.

- **Impact:** Edge cases like Medium's 403-with-content (real article behind
  bot detection) pass; a Cloudflare interstitial that happens to score 0.5
  also passes.
- **Code:** [src/crawler/persist_gate.py:25](src/crawler/persist_gate.py:25)
  (`_BLOCK_CONFIDENCE_FLOOR = 0.5`).
- **Direction:** Sample 50–100 URLs per HTTP code/domain combination,
  hand-label real vs garbage, fit a logistic-regression-style threshold.
  Per-domain overrides for the worst offenders (Cloudflare-fronted SaaS).

### Sync `/extract` has graceful fetch-failure; static worker does not

The sync `/extract` route catches `ConnectError`, DNS failures, and firewall
blocks at the fetcher level and returns a 200 with a degraded
`ExtractResult` (`fetcher_used="none"`, `escalation="failed"`). The async
`static_worker._process_one` re-raises any pipeline exception, which sends
the SQS message back for redelivery; after max attempts it goes to the DLQ.

- **Impact:** A URL the static fetcher cannot reach (api.coindesk.com,
  netflixtechblog.com, eng.lyft.com in the 1000-URL run) ends up with no
  row in DDB and a `failed=1` increment per retry attempt — the message
  burns SQS receives instead of being persisted as a degraded marker.
- **Code:** [src/crawler/workers/static_worker.py:121-133](src/crawler/workers/static_worker.py:121)
  (the outer `except Exception:` re-raises).
- **Direction:** Build a degraded `ExtractResult` (mirroring routes.py's
  `_degraded_result`) and feed it through the persist gate. The rejection
  marker captures the failure and stops the SQS retry storm.

---

## Worker / pipeline

### Outer-except path in `_process_one` is not idempotency-gated

`PagesRepo.try_claim_for_job` requires the Pages row to exist. The outer
`except Exception:` branch runs when `extract_pipeline` raises BEFORE any
`pages.put`, so there is no row to claim against. The `failed=1` increment
in that branch can over-count on SQS redelivery.

- **Impact:** A pipeline-crashing URL in a 1000-URL job can inflate
  `failed` by up to SQS `maxReceiveCount` (default 3). Bounded; not as bad
  as the prior `succeeded` over-count, but real.
- **Code:** [src/crawler/workers/static_worker.py:121-133](src/crawler/workers/static_worker.py:121)
  with a `TODO(idempotency)` comment.
- **Direction:** Coupled with the graceful fetch-failure fix above —
  if we persist a degraded marker for fetch failures, this branch
  disappears entirely and the gated path handles it.

### Async path doesn't propagate `escalation*` observability

`POST /extract` (sync) populates `result.escalation`, `result.escalation_meta`,
`result.escalation_error` on the `ExtractResult` and persists those alongside
the row. The async path does not: the headless worker persists with default
`escalation="not_attempted"` when it succeeds, and the static worker doesn't
back-fill the field after a successful escalation.

- **Impact:** Querying DDB for "what fraction of last week's batch URLs
  needed headless" is impossible from the persisted data alone; you have
  to scrape CloudWatch logs.
- **Code:** [src/crawler/workers/static_worker.py:38-49](src/crawler/workers/static_worker.py:38)
  (existing comment block explains the deferred work).
- **Direction:** Pass an `escalated_from` hint into `invoke_headless` and
  have the headless worker set the escalation fields on its persisted
  row; or post-update the row after escalation success.
- **Note:** The sibling drop of `result.errors` from put/get was a
  related symptom and is now fixed in
  [src/crawler/storage/dynamo.py](src/crawler/storage/dynamo.py)
  (the `persistence_rejected:<reason>` audit tag now survives the
  round-trip). The `escalation`, `escalation_error`, `escalation_meta`,
  and `body_text` fields remain dropped and are still in scope above.

### High-confidence captcha bypass

`is_likely_captcha` is only consulted by the persist gate when the HTTP
status passes. If a Cloudflare interstitial returns HTTP 200 and the
extractor happens to score `confidence > 0.5` on the boilerplate ("Just a
moment…" content has consistent meta tags), the gate's status-based rules
don't fire and only the captcha-body rule catches it. Two false-negative
shapes remain: (a) status 200 + boilerplate that doesn't match the captcha
fingerprint regex, (b) status 403 with `confidence >= 0.5` that's also a
captcha — currently let through by the confidence override.

- **Impact:** Some bot-block pages still slip through as persisted "real"
  content with invented topics. Smaller than the pre-fix 26% rate but not
  zero.
- **Code:** [src/crawler/persist_gate.py](src/crawler/persist_gate.py),
  [src/crawler/fetcher/confidence.py](src/crawler/fetcher/confidence.py)
  (`is_likely_captcha`).
- **Direction:** Expand the captcha fingerprint patterns (Akamai, AWS WAF,
  PerimeterX, Datadome, Imperva). Make the captcha check run BEFORE the
  confidence-override gate.

---

## Storage

### `try_claim_for_job` can create a ghost Pages row

When the workers call `pages.try_claim_for_job(url_hash, job_id)` against a
non-existent Pages row, DDB's `ADD` creates a minimal item containing only
`url_hash`, `version`, and `counted_job_ids`. The mainline worker code
always `put`s the row before claiming, so this only happens if a worker
crashes between claim and put — a narrow window.

- **Mitigation in place:** [src/crawler/storage/dynamo.py](src/crawler/storage/dynamo.py)'s
  `PagesRepo.get` returns `None` when the `url` attribute is missing, so
  external callers never see ghost rows. The audit trail is preserved
  through the `counted_job_ids` attribute on the ghost.
- **Direction:** A periodic sweeper that finds rows lacking `url` and
  either deletes them or marks them for retry. Not built; not urgent at
  PoC scale.

### Pages table pins `version=0`; no recrawl history

The DDB Pages table is keyed `(url_hash, version)`. All writes today pin
`version=0`. Recrawling the same URL overwrites the prior row — the API
exposes no way to read "what did this page look like last month".

- **Impact:** "Before fix / after fix" comparisons on a URL require
  manual S3 archeology. Discussed at length earlier in this session.
- **Code:** [src/crawler/storage/dynamo.py:48](src/crawler/storage/dynamo.py:48)
  (`"version": 0` hardcoded in put).
- **Direction:** Read max version for `url_hash` via the existing range
  key, increment, write the new row. Expose `?version=latest|all|N` on
  `/pages`. Schema already supports it.

### `counted_job_ids` set grows unbounded

Each Pages row accumulates job IDs across every job that has ever counted
it. At PoC scale this is fine (a URL is in <10 jobs). At billion-URL scale
the SS attribute would dominate the item size.

- **Impact:** Item size grows ~36 bytes per job that recrawled the URL.
  Acceptable below a few hundred jobs per URL.
- **Direction:** Move idempotency to a separate `JobUrls` table with TTL
  (e.g. 90 days). Keeps the Pages row content-only.

---

## Infrastructure

### AWS Lambda concurrency capped at 10

The deploy account is on the default Lambda quota of 10 concurrent executions.
The 10-unreserved-minimum rule blocks `ReservedConcurrentExecutions` on any
function, so the static worker can starve the API Lambda mid-burst (visible
as API Gateway 503s during polling in the 1000-URL test).

- **Code:** [infra/template.yaml](infra/template.yaml) has the
  `ReservedConcurrentExecutions` line commented out;
  [docs/deploy.md §5](docs/deploy.md) documents the quota.
- **Direction:** Service Quotas raise on "Lambda concurrent executions".
  Then uncomment `ReservedConcurrentExecutions=20` on the static worker
  and consider reserving slots on the API Lambda too.

### No per-domain politeness / rate-limit awareness

Workers fetch as fast as they can. There is no token bucket per domain,
no robots.txt cache, no backoff on 429.

- **Impact:** Burst submissions to the same domain (e.g. 100 arxiv URLs)
  trigger 429s upstream — visible as `upstream_rate_limited` rejections in
  the persist gate.
- **Direction:** Per-domain SQS shards + DDB-backed token bucket. Spec'd
  in [docs/part-2-scale-design.md §4](docs/part-2-scale-design.md).
  Not built at PoC scale.

### Poll-only job status; no push notifications

Clients poll `GET /jobs/{id}` every N seconds until terminal state.
Each poll is a Lambda invocation. A 1000-URL job at 5s poll cadence
burns ~120 API Lambda invocations purely on status checks.

- **Direction:** SNS topic per job, or DynamoDB Streams + AppSync
  subscription, or webhook URL the client registers at submit time.
  Eliminates the read-during-write contention.

---

## Observability

### No CloudWatch metric for rejection rate

The persist gate writes rejected markers but doesn't emit a metric for the
rejection rate per minute / per domain. Operators must query DDB to know
how much origin-blocking is happening.

- **Direction:** `boto3.client("cloudwatch").put_metric_data` from
  [src/crawler/persist_gate.py](src/crawler/persist_gate.py) when a
  rejection fires. Dimensions: `reason`, `domain`. Cheap.

### Pre-existing: no DLQ alarm wired to a pager

If the static worker DLQ depth goes > 0, nothing alarms. Documented in
[docs/part-2-scale-design.md §7](docs/part-2-scale-design.md) as a
prod-readiness gate but not built.

---

## Test coverage gaps

- No end-to-end test runs against a deployed stack in CI. Smoke tests in
  `scripts/smoke.sh` are manual.
- `tests/eval/` accuracy regression suite covers only the 3 hand-curated
  fixture URLs (Amazon, REI, CNN). No broader URL diversity in the eval
  set.
- The persist-gate's rejection-marker rows are not yet exercised by the
  eval suite — should add fixtures for "Cloudflare 403" / "Cloudflare
  202 interstitial" / "rate limit 429" so future regressions on the gate
  are caught.

---

## How this list is maintained

Append a new entry whenever you encounter an issue you cannot fix in the
current change. Each entry needs: title, impact, code/infra pointer,
direction. Mark resolved entries as `[RESOLVED in <SHA>]` and leave them
for one quarter before deleting, so reviewers can see what was retired.
