# Module: `extractor`

## Purpose

Parse HTML into structured fields. Every function here is pure — given
HTML in, structured data out, no I/O, no global state, no exceptions for
ordinary malformed input.

## Files

| File | One-liner |
|---|---|
| `meta.py` | `extract_meta(html, *, soup=None) -> MetaTags`: `<title>`, `<meta name/property>`, OG, Twitter Card, canonical, keywords, h1 (≤3). |
| `jsonld.py` | `extract_jsonld(html, *, soup=None) -> list[dict]`: every parseable `<script type="application/ld+json">` block. |
| `body.py` | `extract_body(html) -> str \| None`: main body text via `trafilatura` (boilerplate-stripped). |
| `language.py` | `detect_language(text) -> str \| None`: `langdetect` with seeded determinism; min 20 chars. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.extractor.meta.extract_meta(html: str, *, soup: BeautifulSoup | None = None) -> MetaTags`
- `crawler.extractor.meta.MetaTags` — dataclass: `title`, `description`, `canonical_url`, `keywords`, `open_graph`, `twitter_card`, `h1` (list, up to 3), `raw_meta`.
- `crawler.extractor.jsonld.extract_jsonld(html: str, *, soup: BeautifulSoup | None = None) -> list[dict[str, Any]]`
- `crawler.extractor.body.extract_body(html: str) -> str | None`
- `crawler.extractor.language.detect_language(text: str | None) -> str | None`

Pipeline parses `BeautifulSoup(html, "lxml")` once and passes the same `soup`
to both `extract_meta` and `extract_jsonld` to avoid the second parse.
Callers that supply only raw HTML still work — both extractors fall back to
parsing internally if `soup` is omitted.

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

`tests/unit/test_extractor_*.py` — one suite per submodule (`test_extractor_meta`,
`test_extractor_jsonld`, `test_extractor_body`, `test_extractor_language`).
Saved HTML fixtures live in `tests/fixtures/` and are used to assert specific
field extraction.
