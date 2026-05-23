# Module: `classifier`

## Purpose

Turn extraction output (meta tags, JSON-LD, body text) into a ranked
top-K list of `Topic` objects. Two candidate generators feed one fuser.

## Files

| File | One-liner |
|---|---|
| `heuristics.py` | `candidates_from_meta_and_jsonld(meta, jsonld) -> list[TopicCandidate]` — high-precision tags + title/h1/description signal. |
| `keyphrases.py` | `extract_keyphrases(text, *, language)` — language-aware YAKE keyphrases from body. |
| `fuse.py` | `fuse_topics(heuristic, keyphrase, *, top_k=10) -> list[Topic]` — merge, sum-weight, near-dup merge, normalize. |
| `stopwords.py` | Shared stopword set + `is_all_stopwords(label)` filter used by heuristics + keyphrases. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.classifier.heuristics.candidates_from_meta_and_jsonld(meta, jsonld) -> list[TopicCandidate]`
- `crawler.classifier.heuristics.TopicCandidate` — frozen dataclass: `label`, `weight`, `source`.
- `crawler.classifier.keyphrases.extract_keyphrases(text, *, language="en", max_keyphrases=20) -> list[KeyphraseCandidate]`
- `crawler.classifier.keyphrases.KeyphraseCandidate` — frozen dataclass: `label`, `weight`.
- `crawler.classifier.fuse.fuse_topics(heuristic, keyphrase, *, top_k=10) -> list[Topic]`
- `crawler.classifier.fuse.Topic` — dataclass: `label`, `score` (∈[0,1] after normalize), `sources`.
- `crawler.classifier.stopwords.STOPWORDS`, `is_all_stopwords(label)`.

## Weighting

Heuristic weights (`heuristics.py`):

| Signal | Weight |
|---|---|
| JSON-LD `category`, `keywords` | 2.0 |
| `<meta name="keywords">` entries | 1.5 |
| Title — phrase chunk (split on `:|—–•·()[]`) | 1.5 |
| OpenGraph type / category / tag | 1.4 |
| H1 — phrase chunk | 1.3 |
| Title — content token (≥4 chars, non-stopword) | 1.0 |
| JSON-LD `@type` | 1.0 |
| Description — phrase chunk | 1.0 |
| H1 — content token | 0.9 |
| Description — content token | 0.6 |

Stopword filter (`stopwords.py`): drops candidates whose label is entirely
function words or web boilerplate ("click", "more", "cookies", "pmid", …).
Applies to both heuristic token/phrase candidates and YAKE keyphrases.

YAKE (`keyphrases.py`):
- Per-language extractor cached via `lru_cache` keyed on the detected
  language code (mapped from `langdetect` → YAKE codes; `cs → cz`).
- Bodies shorter than 120 chars or in languages YAKE doesn't ship
  stopwords for return `[]` (avoids garbage output and wasted CPU).
- Weight: `1.0 / (yake_score + 0.2)` — raw YAKE scores are
  lower-is-better; this inverts and stays smooth across the range
  (avoids saturation; commit 6aac7a6).

Fuser (`fuse.py`):
1. Normalize label (`" ".join(strip().lower().split())`).
2. Sum weights across all sources sharing that label.
3. Sort desc.
4. Merge near-duplicates: collapse topics with token-set overlap ≥ 0.7 on
   the shorter label (catches "python" / "python software foundation").
5. Slice to `top_k`.
6. Normalize so the top entry's score is `1.0`.

## Dependencies

- `crawler.extractor.meta.MetaTags` — input type for heuristics.
- External: `yake`.

## Tests

`tests/unit/test_classifier_heuristics.py`, `tests/unit/test_classifier_keyphrases.py`,
`tests/unit/test_classifier_fuse.py`. End-to-end ranking is exercised through
`tests/unit/test_pipeline.py`.

Accuracy regression suite: `tests/eval/test_topic_accuracy.py` runs the
full pipeline over ~20 labeled fixtures (`tests/eval/fixtures.yaml`) and
asserts that aggregate `top1` / `top3_hit_rate` / `mrr` don't regress
past `TOLERANCE=0.05` against `tests/eval/baseline.json`. Update the
baseline after an intentional improvement with:
`.local/venv/bin/python -m tests.eval.test_topic_accuracy --update-baseline`.
