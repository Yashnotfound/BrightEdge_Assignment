# Module Index

Navigation map for `src/crawler/`. Read this first; then jump to the
specific module doc for any subpackage you need to change.

## Top-level package: `src/crawler/`

| File / Module | Doc | Purpose |
|---|---|---|
| `pipeline.py` | (this file, §Pipeline below) | Orchestrates fetch → extract → classify into one `ExtractResult`. |
| `config.py` | (this file, §Config below) | Env-driven `Settings` dataclass loaded by every Lambda. |
| `fixtures.py` | (this file, §Fixtures below) | Hard-coded fallback responses (Amazon, REI, CNN) for `?fixture=1` demo mode. |
| `persist_gate.py` | (see `api.md` / `workers.md`) | Pure helpers (`reject_reason`, `to_rejected`) that filter 4xx/5xx/captcha responses into rejected-marker rows before they reach DDB. |
| `__init__.py` | — | Empty marker. |
| [`api/`](api.md) | api.md | FastAPI app, routes (`/extract`, `/batch`, `/jobs`, `/pages`), Pydantic schemas. |
| [`classifier/`](classifier.md) | classifier.md | Heuristic candidates, YAKE keyphrases, topic fusion. |
| [`extractor/`](extractor.md) | extractor.md | Meta tags, OpenGraph, JSON-LD, body text, language detection. |
| [`fetcher/`](fetcher.md) | fetcher.md | Static HTTP fetcher, headless Lambda invoker, confidence scorer, robots.txt, UA rotation. |
| [`storage/`](storage.md) | storage.md | DynamoDB (Pages/Jobs), S3 (raw HTML/JSON-LD), URL hashing. |
| [`workers/`](workers.md) | workers.md | SQS static-worker + direct-invoke headless-worker Lambdas. |

## Call graph (high level)

```
api.routes.extract
  -> pipeline.extract_pipeline
       -> fetcher.static.fetch
       -> extractor.{meta,jsonld,body,language}
       -> fetcher.confidence.{score_confidence,is_likely_captcha}
       -> classifier.{heuristics,keyphrases,fuse}
  -> (if confidence < threshold) fetcher.headless.invoke_headless -> workers.headless_worker.handler
  -> storage.s3.RawHtmlStore + storage.dynamo.PagesRepo

api.routes.batch
  -> sqs.send_message_batch
  -> workers.static_worker.handler -> pipeline.extract_pipeline -> storage.*
```

## Pipeline (`src/crawler/pipeline.py`)

- `extract_pipeline(url, *, return_html=False)` — async; fetches with static HTTPX, then runs `_process`. Returns `ExtractResult` (or `(ExtractResult, html)`).
- `process_html(*, url, html, http_status, content_type, fetcher_used)` — sync; entry point for callers that already have HTML (used by `workers/headless_worker.py`).
- `process_html_timed(...)` — same signature as `process_html`, but also returns a `dict[str, float]` of per-stage wall-clock timings in ms. Used by `scripts/bench_pipeline.py`. Production code paths should use `process_html`; the same per-stage numbers are emitted via `logger.info("pipeline.timing", …)` for log-based observability.
- Body text is truncated to `_BODY_TEXT_LIMIT` (50KB) before being placed on the result.

## Config (`src/crawler/config.py`)

Single frozen `Settings` dataclass populated from env vars:

| Env var | Field | Default |
|---|---|---|
| `PAGES_TABLE` | `pages_table` | `brightedge-pages` |
| `JOBS_TABLE` | `jobs_table` | `brightedge-jobs` |
| `RAW_HTML_BUCKET` | `raw_html_bucket` | `""` |
| `JOBS_BUCKET` | `jobs_bucket` | `""` |
| `STATIC_QUEUE_URL` | `static_queue_url` | `""` |
| `HEADLESS_FUNCTION_NAME` | `headless_function_name` | `""` |
| `AWS_REGION` | `aws_region` | `us-east-1` |
| `CONFIDENCE_THRESHOLD` | `confidence_threshold` | `0.5` |

Empty string defaults are intentional — they let local dev skip persistence.

## Fixtures (`src/crawler/fixtures.py`)

Three hand-curated `ExtractResult` builders, one per assignment test URL:

- `amazon_toaster()` — Cuisinart CPT-122 product page (anti-bot fallback).
- `rei_outdoors()` — REI Co-op blog post on introducing a friend to the outdoors.
- `cnn_tech()` — CNN article on AI's impact on tech jobs.

Selected in `api.routes.extract` when `?fixture=1` is set and the requested URL
matches one of the three. Each fixture sets `fetcher_used="fixture"` and an
`errors[]` entry that documents the fallback so reviewers see exactly what
happened.

## Tests

Tests live under `tests/unit/` (one file per module) and `tests/eval/`
(accuracy regression suite over labeled fixtures). Run with:

```bash
pytest                            # all
pytest tests/unit/test_pipeline.py
pytest -k classifier
```

## Conventions for changes

- New top-level file under `src/crawler/`: add a row to the table above.
- New submodule under `src/crawler/<existing>/`: edit `docs/modules/<existing>.md` only.
- New module directory under `src/crawler/`: create `docs/modules/<new>.md`
  using the same five-section template (Purpose, Files, Public API,
  Dependencies, Tests).
