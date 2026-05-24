# Module: `workers`

## Purpose

Lambda entry points for the async crawl path. The static worker is
SQS-triggered and runs the standard pipeline; the headless worker is
direct-invoked and runs Playwright/chromium inside a container image.

## Files

| File | One-liner |
|---|---|
| `static_worker.py` | SQS-triggered batch processor; calls `pipeline.extract_pipeline`, persists to S3+DDB, bumps job counters. |
| `headless_worker.py` | Direct-invoke (`{url, persist?}`); fetches HTML via Playwright pointed at sparticuz/chromium (`CHROMIUM_EXECUTABLE` env var = `/opt/chromium/chromium`), Lambda-hardened launch flags, `wait_until="networkidle"` with a 15s page-load timeout followed by a bounded `wait_for_function` poll for `document.body.innerText.length > 200` (3s cap) so React/Vue SPAs have a chance to hydrate, then runs `pipeline.process_html`. Worst-case wait is ~18s; Lambda timeout is 60s. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.workers.static_worker.handler(event, context) -> dict` — Lambda SQS handler. Uses `aws_lambda_powertools.utilities.batch.BatchProcessor` for partial-failure reporting.
- `crawler.workers.headless_worker.handler(event, context=None) -> dict` — direct-invoke handler. `event = {"url": str, "persist": bool}`. Returns the `ExtractResult.model_dump(mode="json")`.

## Event shapes

**static_worker** — each SQS message body:

```json
{ "url": "https://example.com/page", "job_id": "abc123" }
```

`job_id` is optional; if present, `JobsRepo.increment(succeeded|failed)` is
called per message.

**headless_worker** — direct invoke payload:

```json
{ "url": "https://example.com/page", "persist": true }
```

`persist=False` is used by the sync `/extract` path (the API persists once
based on whichever result wins). `persist=True` is used by the static
worker when it escalates.

## Confidence escalation

`static_worker._process_one` calls `invoke_headless(url, persist=True)`
when the static `extraction_confidence` is below
`settings.confidence_threshold` and `HEADLESS_FUNCTION_NAME` is set.
Headless persists itself in that path, so the static worker skips its
own persist on successful escalation.

**Observability note:** the sync `POST /extract` path tracks escalation
outcome on the response via `ExtractResult.escalation` /
`escalation_meta` / `escalation_error` (5 states: `not_attempted`,
`skipped`, `succeeded`, `no_improvement`, `failed`). The async path
here does NOT yet mirror that — when headless succeeds in the async
flow, the headless worker persists its own `ExtractResult` with the
default `escalation: "not_attempted"`, and when escalation fails the
static worker falls through to persist the static result without
flagging the failure on the row. See the comment block in
`_process_one` for the deferred work.

## Idempotent counter bumps

SQS delivers each message at-least-once. Without a guard, a worker that
completes the extract, bumps `JobsRepo.increment`, and then crashes
before SQS-acking will be redelivered — the second attempt would
double-count the URL. The static worker calls
`PagesRepo.try_claim_for_job(url_hash, job_id)` before each
`jobs.increment(...)` site (escalation-success, normal-persist,
persist-gate rejection). The claim is an atomic `ADD` to a
`counted_job_ids` String Set on the Pages row; the first claim returns
`True` and the bump runs, redelivered claims return `False` and the bump
is skipped. The outer `except` branch in `_process_one` (extract
pipeline raised) is deliberately NOT gated — no Pages row exists at that
point and the path is rare; see the `TODO(idempotency)` comment.

## Persist gate

Both workers run `crawler.persist_gate.reject_reason` right before the
S3/DDB write. On a 4xx/5xx block or captcha-fingerprint body the result
is swapped for a rejected marker (`fetcher_used="rejected"`, empty topics,
zero confidence). The static worker also bumps `failed` (not `succeeded`)
on its `JobsRepo` so job-completion counters reflect that the extraction
was garbage. The S3 raw-HTML write is skipped in both paths; the DDB row
is still written so `/pages` keeps the audit trail.

## Dependencies

- `crawler.pipeline.extract_pipeline` (static) and `crawler.pipeline.process_html` (headless).
- `crawler.fetcher.headless.invoke_headless` (static-worker escalation).
- `crawler.persist_gate` — `reject_reason` / `to_rejected` (filter garbage before DDB).
- `crawler.storage.dynamo.{PagesRepo,JobsRepo}`, `crawler.storage.s3.RawHtmlStore`.
- `crawler.config.load_settings`.
- External: `aws_lambda_powertools` (static), `playwright` (headless; imported lazily inside the handler).

## Tests

`tests/unit/test_static_worker.py` mocks the SQS event shape and asserts
DDB/S3 side effects + headless-escalation branching. The headless worker
has **no dedicated unit suite** — its launch flags, sparticuz integration,
and persistence path are validated end-to-end via `scripts/smoke.sh`
against the deployed stack rather than mocked locally.

## Deployment

- **static_worker**: regular zip-package Lambda; trigger = `STATIC_QUEUE_URL`.
- **headless_worker**: container-image Lambda (`infra/headless.Dockerfile`);
  ARN exposed to the API + static-worker via `HEADLESS_FUNCTION_NAME` env var.
  Required env vars (`infra/template.yaml`):
  - `CHROMIUM_EXECUTABLE=/opt/chromium/chromium` — read by `_fetch_headless`
    and passed to `chromium.launch(executable_path=...)`. The chromium binary
    is the *only* one we actually point Playwright at; `headless.Dockerfile`
    bakes this via an `ENV` directive too, the template export is a belt-and-
    braces fallback.
  - `HOME=/tmp` — Lambda's runtime user can only write under `/tmp`, and
    chromium creates scratch files on startup.
  - `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` — set but **not consumed**
    by the worker (it passes `executable_path` directly). Kept to suppress
    Playwright's startup warning about a missing bundled-browser cache.
