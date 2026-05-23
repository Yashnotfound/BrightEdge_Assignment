# Module: `api`

## Purpose

HTTP surface of the crawler. FastAPI app, routes for sync extract / async
batch / cached pages, and Pydantic schemas that double as the API contract
and the in-process result type.

## Files

| File | One-liner |
|---|---|
| `main.py` | FastAPI app factory + `/` (HTML demo) + `/health` + Mangum `handler` for Lambda. |
| `routes.py` | `/extract`, `/extract?fixture=1`, `/pages`, `/pages/{url_hash}`, `/batch`, `/jobs/{job_id}`. Data endpoints carry `Depends(require_api_key)`. |
| `auth.py` | `require_api_key` FastAPI dependency built on `fastapi.security.HTTPBearer`. No-op when `API_KEY` env is empty; otherwise enforces `Authorization: Bearer <key>` with `hmac.compare_digest`. Registering via `HTTPBearer` puts the scheme in the OpenAPI doc so Swagger UI renders an Authorize button. |
| `schemas.py` | `ExtractResult`, `Topic`, `ExtractRequest`, `BatchRequest`, `BatchResponse`, `JobStatus`. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.api.main.app` — the FastAPI instance (use with `uvicorn`).
- `crawler.api.main.handler` — Mangum Lambda adapter wrapping `app`.
- `crawler.api.routes.router` — the `APIRouter` mounted on `app`.
- `crawler.api.auth.require_api_key` — Bearer dependency attached to every
  data route; also provides the OpenAPI security scheme so Swagger UI at
  `/docs` renders an Authorize button (paste key once, all "Try it out"
  calls send the header automatically).
- `crawler.api.schemas.ExtractResult` — single source of truth for the
  extract response shape; also used internally by the pipeline.

## Routes (current)

| Method | Path | Returns | Notes |
|---|---|---|---|
| GET | `/` | `text/html` | Static demo page (or fallback HTML). |
| GET | `/health` | `{"status":"ok"}` | Liveness check. |
| POST | `/extract?fixture=0\|1` | `ExtractResult` | Sync extract; `fixture=1` returns saved Amazon response. |
| GET | `/pages?url=…` | `ExtractResult` | DDB-cached extract by URL. |
| GET | `/pages/{url_hash}` | `ExtractResult` | DDB-cached extract by SHA-256 hash. |
| POST | `/batch` | `BatchResponse` | Enqueue up to 1000 URLs onto SQS. |
| GET | `/jobs/{job_id}` | `JobStatus` | Poll status of a `/batch` submission. |

## Dependencies

- `crawler.pipeline` — `extract_pipeline` (sync extract path).
- `crawler.fetcher.headless` — `invoke_headless` (low-confidence escalation).
- `crawler.storage.{dynamo,s3,hashing}` — persistence.
- `crawler.config` — env-driven settings.
- `crawler.fixtures` — `amazon_toaster` (only loaded under `?fixture=1`).
- External: `fastapi`, `mangum`, `boto3` (SQS for `/batch`).

## Tests

`tests/test_api*.py` covers the routes; `tests/test_pipeline.py` covers
the end-to-end `/extract` flow. Smoke tests live in `scripts/smoke.sh`.
