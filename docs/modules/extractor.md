# Module: `extractor`

## Purpose

Parse HTML into structured fields. Every function here is pure — given
HTML in, structured data out, no I/O, no global state, no exceptions for
ordinary malformed input.

## Files

| File | One-liner |
|---|---|
| `meta.py` | `extract_meta(html) -> MetaTags`: `<title>`, `<meta name/property>`, OG, Twitter Card, canonical, keywords. |
| `jsonld.py` | `extract_jsonld(html) -> list[dict]`: every parseable `<script type="application/ld+json">` block. |
| `body.py` | `extract_body(html) -> str \| None`: main body text via `trafilatura` (boilerplate-stripped). |
| `language.py` | `detect_language(text) -> str \| None`: `langdetect` with seeded determinism; min 20 chars. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.extractor.meta.extract_meta(html: str) -> MetaTags`
- `crawler.extractor.meta.MetaTags` — dataclass: `title`, `description`, `canonical_url`, `keywords`, `open_graph`, `twitter_card`, `raw_meta`.
- `crawler.extractor.jsonld.extract_jsonld(html: str) -> list[dict[str, Any]]`
- `crawler.extractor.body.extract_body(html: str) -> str | None`
- `crawler.extractor.language.detect_language(text: str | None) -> str | None`

## Guarantees

- `extract_meta` never raises on malformed HTML; missing fields stay `None` / empty.
- `extract_jsonld` silently drops blocks that fail `json.loads`.
- `extract_body` returns `None` on too-sparse content (trafilatura's call).
- `detect_language` returns `None` if input is empty, < 20 chars, or detection fails (`LangDetectException`).

## Dependencies

- External only: `beautifulsoup4` + `lxml` (meta, jsonld), `trafilatura` (body),
  `langdetect` (language).
- No intra-crawler imports — extractors are leaves of the dependency graph.

## Tests

`tests/test_extractor_*.py` for each submodule. Saved HTML fixtures live in
`tests/fixtures/` and are used to assert specific field extraction.
