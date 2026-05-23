# Module Index

Navigation map for `src/crawler/`. Read this first; then jump to the
specific module doc for any subpackage you need to change.

## Top-level package: `src/crawler/`

| File / Module | Doc | Purpose |
|---|---|---|
| `pipeline.py` | (this file, §Pipeline below) | Orchestrates fetch → extract → classify into one `ExtractResult`. |
| `config.py` | (this file, §Config below) | Env-driven `Settings` dataclass loaded by every Lambda. |
| `fixtures.py` | (this file, §Fixtures below) | Hard-coded Amazon response for `?fixture=1` demo fallback. |
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

`amazon_toaster() -> ExtractResult` returns a hand-curated extraction for the
Cuisinart toaster URL so the `?fixture=1` demo path works without hitting
Amazon's anti-bot wall.

## Tests

Tests live under `tests/` (mirrored layout where useful). Run with:

```bash
pytest                       # all
pytest tests/test_pipeline.py
pytest -k classifier
```

## Conventions for changes

- New top-level file under `src/crawler/`: add a row to the table above.
- New submodule under `src/crawler/<existing>/`: edit `docs/modules/<existing>.md` only.
- New module directory under `src/crawler/`: create `docs/modules/<new>.md`
  using the same five-section template (Purpose, Files, Public API,
  Dependencies, Tests).
