# Module: `storage`

## Purpose

Persist `ExtractResult`s. Raw HTML and JSON-LD go to S3; structured
result + topics go to DynamoDB. URL normalization + hashing is also
here because it's the partition key.

## Files

| File | One-liner |
|---|---|
| `dynamo.py` | `PagesRepo` and `JobsRepo` — DDB accessors for cached extracts and batch-job state. |
| `s3.py` | `RawHtmlStore` — gzipped raw HTML + JSON-LD writers, date-partitioned keys. |
| `hashing.py` | `normalize_url(url)` and `url_hash(url)` — SHA-256 of normalized URL. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.storage.dynamo.PagesRepo(table_name, region_name="us-east-1")` — frozen dataclass.
  - `put(result, *, s3_html_uri, s3_jsonld_uri) -> None`
  - `get(*, url_hash) -> ExtractResult | None`
- `crawler.storage.dynamo.JobsRepo(table_name, region_name="us-east-1")` — frozen dataclass.
  - `create(*, job_id, total) -> None`
  - `increment(*, job_id, succeeded=0, failed=0) -> None`
  - `get(*, job_id) -> JobStatus | None`
- `crawler.storage.s3.RawHtmlStore(bucket)` — frozen dataclass.
  - `put_raw_html(*, url_hash, domain, fetched_at_iso, html) -> str` (s3:// URI)
  - `put_jsonld(*, url_hash, domain, fetched_at_iso, jsonld) -> str` (s3:// URI)
- `crawler.storage.hashing.normalize_url(url) -> str`
- `crawler.storage.hashing.url_hash(url) -> str` — SHA-256 hex digest.

## S3 key layout

```
raw/domain={domain}/year=YYYY/month=MM/day=DD/{url_hash}.html.gz
jsonld/domain={domain}/year=YYYY/month=MM/day=DD/{url_hash}.jsonld.json
```

Raw HTML is gzipped (`ContentEncoding: gzip`). JSON-LD is plain JSON.

## DynamoDB schema

- **Pages table**: HASH `url_hash` + RANGE `version` (always `0` at PoC).
  GSI `by-domain` (HASH `domain`, RANGE `fetched_at`).
- **Jobs table**: HASH `job_id`. Status moves
  `queued → running → partial|complete|failed`.

Items use `_to_decimal_safe` to coerce floats to `Decimal` (DDB requirement).

## URL normalization

`normalize_url` lowercases scheme + host, drops the fragment, sorts query
params (`parse_qsl(..., keep_blank_values=True)` then `urlencode(sorted)`),
and strips default ports.

## Dependencies

- `crawler.api.schemas` — `ExtractResult`, `JobStatus`, `Topic` (round-tripped through DDB).
- External: `boto3`, stdlib (`hashlib`, `gzip`, `decimal`, `urllib.parse`).

## Tests

`tests/test_storage_hashing.py` (pure-function), `tests/test_storage_dynamo.py`
and `tests/test_storage_s3.py` (boto3 mocked via `moto` or stub clients).
