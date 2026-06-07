# Module: `api`

## Purpose

HTTP surface of the crawler. FastAPI app, routes for sync extract / async
batch / cached pages, and Pydantic schemas that double as the API contract
and the in-process result type.

## Files

| File | One-liner |
|---|---|
| `main.py` | FastAPI app factory + `/` (HTML demo) + `/health` + Mangum `handler` for Lambda. |
| `routes.py` | `/extract` (+ `?fixture=1` shortcut that returns a saved response when the URL matches Amazon/REI/CNN), `/pages`, `/pages/{url_hash}`, `/batch`, `/jobs/{job_id}`. Data endpoints carry `Depends(require_api_key)`. |
| `auth.py` | `require_api_key` FastAPI dependency built on `fastapi.security.HTTPBearer`. No-op when `API_KEY` env is empty; otherwise enforces `Authorization: Bearer <key>` with `hmac.compare_digest`. Registering via `HTTPBearer` puts the scheme in the OpenAPI doc so Swagger UI renders an Authorize button. |
| `schemas.py` | `ExtractResult`, `Topic`, `ExtractRequest`, `BatchRequest`, `BatchResponse`, `JobStatus`. `ExtractRequest.url` and `BatchRequest.urls` carry `field_validator`s that call `crawler.fetcher.url_safety.validate_url` — unsafe URLs (RFC1918, loopback, link-local incl. AWS metadata, etc.) surface as 422 with the bad field highlighted. /batch rejects the whole batch on any unsafe URL. |
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
  Carries three diagnostic fields populated by the sync `/extract`
  handler:
  - `escalation: Literal["not_attempted","skipped","succeeded","no_improvement","failed"]`
    — what the system did about a low-confidence static result OR a
    static-fetch *failure*. Default `"not_attempted"` is set when
    static confidence ≥ `CONFIDENCE_THRESHOLD`.
  - `escalation_error: str | None` — short tag like
    `"lambda:TooManyRequestsException"`, `"TimeoutError"`, or
    `"fetch_failed:FetchTimeoutError"`. Only the exception class /
    Lambda error code is exposed; full traceback goes to CloudWatch
    via `logger.exception` rather than into the response body.
  - `escalation_meta: dict[str, Any]` — `{"headless_confidence":
    float, "headless_word_count": int}` populated whenever headless
    was attempted (success OR no-improvement). For the static-fetch
    *failure* rescue path it additionally contains `"reason":
    "static_fetch_failed"` and `"static_error": "<ExcClass>"` so an
    operator can distinguish a low-confidence escalation from a
    rescue. Empty dict otherwise.

## Graceful upstream-failure handling

The sync `/extract` handler bounds the whole request with a
`time.monotonic()`-relative deadline (~23s of the API Lambda's 28s
timeout, leaving room for headless rescue + serialization). The
deadline is threaded through `extract_pipeline` → `fetcher.static.fetch`
so per-attempt httpx timeouts and retry budgets cannot exceed it.

When the static fetcher fails (timeout, DNS, firewall/CDN block, etc.):

1. If headless is configured AND ≥8s wall-clock remains, the handler
   invokes the headless Lambda as a rescue. Success → 200 with
   `escalation: "succeeded"`, `escalation_meta.reason ==
   "static_fetch_failed"`.
2. Otherwise (no headless / over-budget / headless also errored), the
   handler returns **200 OK** with a degraded `ExtractResult`:
   `fetcher_used == "none"`, `http_status == 0`,
   `extraction_confidence == 0.0`, `escalation == "failed"`,
   `escalation_error == "fetch_failed:<ExcClass>"`, and an `errors[]`
   list containing `static_fetch_failed:<Exc>` (and, when applicable,
   `headless_fetch_failed:<Exc>`).

The deliberate non-decision: a fetcher failure does **not** surface as
HTTP 5xx. The API itself worked; the upstream URL is what we couldn't
reach. Clients must inspect `escalation == "failed"` (or
`fetcher_used == "none"`) to detect this case rather than relying on
the HTTP status code.

## Routes (current)

| Method | Path | Returns | Notes |
|---|---|---|---|
| GET | `/` | `text/html` | Static demo page (or fallback HTML). |
| GET | `/health` | `{"status":"ok"}` | Liveness check. |
| POST | `/extract?fixture=0\|1` | `ExtractResult` | Sync extract; `fixture=1` returns the saved response for any of the three known test URLs (Amazon, REI, CNN). Unknown URLs fall through to the live fetch. |
| GET | `/pages?url=…` | `ExtractResult` | DDB-cached extract by URL. |
| GET | `/pages/{url_hash}` | `ExtractResult` | DDB-cached extract by SHA-256 hash. |
| POST | `/batch` | `BatchResponse` | Enqueue up to 1000 URLs onto SQS. |
| GET | `/jobs/{job_id}` | `JobStatus` | Poll status of a `/batch` submission. |

## Persist gate

Right before `_persist` writes to S3/DDB, the route runs the persist gate
(`crawler.persist_gate.reject_reason`). A 4xx/5xx upstream block or a
captcha-fingerprint body swaps `result` for a rejected marker
(`fetcher_used="rejected"`, empty topics, zero confidence) and skips the
S3 raw-HTML write. The DDB row is still written so `/pages` keeps an audit
trail. The route returns the rejected marker too, so callers see the
rejection in the response.

## Dependencies

- `crawler.pipeline` — `extract_pipeline` (async; awaited from the `/extract` handler).
- `crawler.fetcher.headless` — `invoke_headless` (low-confidence escalation).
- `crawler.fetcher.url_safety` — `validate_url` (called from `schemas.py` field validators; SSRF guard).
- `crawler.persist_gate` — `reject_reason` / `to_rejected` (filter garbage before DDB), `build_fetch_failed_result` (degraded `ExtractResult` shape for the static-fetch-failure path).
- `crawler.storage.{dynamo,s3,hashing}` — persistence.
- `crawler.config` — env-driven settings.
- `crawler.fixtures` — `amazon_toaster`, `rei_outdoors`, `cnn_tech` (lazy-imported inside the route, only when `?fixture=1` is set).
- External: `fastapi`, `mangum`, `boto3` (SQS for `/batch`).

## Tests

`tests/unit/test_api_routes.py` covers the routes (with an autouse DNS
fixture that makes fake test hostnames resolve to a benign public IP, so
the new SSRF guard doesn't reject them at validation time);
`tests/unit/test_schemas.py` covers the Pydantic field validators directly;
`tests/unit/test_pipeline.py` covers the end-to-end `/extract` flow. Smoke
tests live in `scripts/smoke.sh`.
