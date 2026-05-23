# Module: `workers`

## Purpose

Lambda entry points for the async crawl path. The static worker is
SQS-triggered and runs the standard pipeline; the headless worker is
direct-invoked and runs Playwright/chromium inside a container image.

## Files

| File | One-liner |
|---|---|
| `static_worker.py` | SQS-triggered batch processor; calls `pipeline.extract_pipeline`, persists to S3+DDB, bumps job counters. |
| `headless_worker.py` | Direct-invoke (`{url, persist?}`); fetches HTML via Playwright pointed at sparticuz/chromium (`CHROMIUM_EXECUTABLE` env var = `/opt/chromium/chromium`), Lambda-hardened launch flags, `wait_until="domcontentloaded"`, 10s page-load timeout, then runs `pipeline.process_html`. |
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

## Dependencies

- `crawler.pipeline.extract_pipeline` (static) and `crawler.pipeline.process_html` (headless).
- `crawler.fetcher.headless.invoke_headless` (static-worker escalation).
- `crawler.storage.dynamo.{PagesRepo,JobsRepo}`, `crawler.storage.s3.RawHtmlStore`.
- `crawler.config.load_settings`.
- External: `aws_lambda_powertools` (static), `playwright` (headless; imported lazily inside the handler).

## Tests

`tests/test_static_worker.py` mocks SQS event shape and asserts DDB/S3
side effects. `tests/test_headless_worker.py` stubs `playwright` and checks
that `process_html` is fed the right arguments.

## Deployment

- **static_worker**: regular zip-package Lambda; trigger = `STATIC_QUEUE_URL`.
- **headless_worker**: container-image Lambda (`infra/headless.Dockerfile`);
  ARN exposed to the API + static-worker via `HEADLESS_FUNCTION_NAME` env var.
  Requires `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` and `HOME=/tmp`
  in the Lambda environment so chromium can find the binary and write
  scratch state from the non-root runtime user.
