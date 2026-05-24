# Module: `fetcher`

## Purpose

Pull HTML from the public web (static path) or invoke the Playwright
Lambda (headless path), and decide whether the static result is good
enough to keep. Also: robots.txt politeness and User-Agent rotation.

## Files

| File | One-liner |
|---|---|
| `static.py` | Async HTTPX fetcher with realistic headers, retries, 5MB cap; returns `FetchResult`. |
| `headless.py` | `invoke_headless(url, persist=False)` — sync Lambda invoke of the chromium worker. |
| `confidence.py` | `score_confidence(...)` (0.0–1.0) and `is_likely_captcha(title, body)`. |
| `robots.py` | `can_fetch(url, ua) -> RobotsDecision` with 24h in-process LRU cache. |
| `user_agents.py` | `pick()` returns a random realistic browser UA. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.fetcher.static.fetch(url, *, timeout, max_bytes, retries, deadline) -> FetchResult` — async. When `deadline` is set (a `time.monotonic()`-relative absolute timestamp), per-attempt httpx timeouts are clamped to the remaining budget, and retries stop as soon as the budget would not fit one more attempt. This is the contract the route handler relies on to guarantee the fetcher returns before AWS Lambda kills the process on its own 28s ceiling.
- `crawler.fetcher.static.FetchTimeoutError` — `httpx.TimeoutException` subclass raised when the deadline budget is exhausted (distinct from a network-level timeout).
- `crawler.fetcher.static.FetchResult` — frozen dataclass: `url`, `final_url`, `http_status`, `content_type`, `html`, `fetched_via`.
- `crawler.fetcher.headless.invoke_headless(url, *, persist=False) -> dict` — sync; returns the raw `ExtractResult.model_dump(mode="json")` produced by the headless Lambda. Raises `RuntimeError` when the Lambda response carries `FunctionError`, so error-shaped payloads (`errorType`/`errorMessage`) are never silently returned as if they were ExtractResults.
- `crawler.fetcher.confidence.score_confidence(*, title, body_word_count, has_structured_data, is_captcha) -> float`.
- `crawler.fetcher.confidence.is_likely_captcha(title, body) -> bool`.
- `crawler.fetcher.robots.can_fetch(url, user_agent) -> RobotsDecision`.
- `crawler.fetcher.user_agents.pick() -> str`.

## Confidence rubric

Below `0.5` triggers headless escalation (configurable via
`CONFIDENCE_THRESHOLD`). Scoring is additive, clipped to 1.0:

| Signal | Contribution |
|---|---|
| Has non-empty title | +0.3 |
| Body ≥ 300 words | +0.4 |
| Body 100–299 words | +0.3 |
| Body 20–99 words | +0.2 |
| Has JSON-LD structured data | +0.2 |
| Baseline ("not blocked") | +0.1 |
| CAPTCHA fingerprint detected | overrides to **0.1** |

## Dependencies

- `crawler.config` — only `headless.py` needs it (for `HEADLESS_FUNCTION_NAME`).
- External: `httpx` (static + robots), `protego` (robots), `boto3` (headless invoke).

## Tests

`tests/unit/test_fetcher_static.py` and `tests/unit/test_fetcher_confidence.py`
are the dedicated suites. `robots.py`, `user_agents.py`, and `headless.py`
do **not** have unit tests yet — `robots.can_fetch` is currently best-effort
and not called from the live pipeline; `user_agents.pick` is a 3-element
random selector; and headless invocation is verified end-to-end via
`scripts/smoke.sh` against the deployed stack.
