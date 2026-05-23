# Module: `classifier`

## Purpose

Turn extraction output (meta tags, JSON-LD, body text) into a ranked
top-K list of `Topic` objects. Two candidate generators feed one fuser.

## Files

| File | One-liner |
|---|---|
| `heuristics.py` | `candidates_from_meta_and_jsonld(meta, jsonld) -> list[TopicCandidate]` — high-precision tags. |
| `keyphrases.py` | `extract_keyphrases(text) -> list[KeyphraseCandidate]` — YAKE keyphrases from body. |
| `fuse.py` | `fuse_topics(heuristic, keyphrase, *, top_k=10) -> list[Topic]` — merge, sum-weight, normalize. |
| `__init__.py` | Empty marker. |

## Public API

- `crawler.classifier.heuristics.candidates_from_meta_and_jsonld(meta, jsonld) -> list[TopicCandidate]`
- `crawler.classifier.heuristics.TopicCandidate` — frozen dataclass: `label`, `weight`, `source`.
- `crawler.classifier.keyphrases.extract_keyphrases(text, *, max_keyphrases=20) -> list[KeyphraseCandidate]`
- `crawler.classifier.keyphrases.KeyphraseCandidate` — frozen dataclass: `label`, `weight`.
- `crawler.classifier.fuse.fuse_topics(heuristic, keyphrase, *, top_k=10) -> list[Topic]`
- `crawler.classifier.fuse.Topic` — dataclass: `label`, `score` (∈[0,1] after normalize), `sources`.

## Weighting

Heuristic weights (`heuristics.py`):

| Signal | Weight |
|---|---|
| JSON-LD `category`, `keywords` | 2.0 |
| JSON-LD `@type` | 1.0 |
| `<meta name="keywords">` entries | 1.5 |
| OpenGraph type / category / tag | 1.4 |
| (Title — currently unused at this layer) | 1.0 |

YAKE weight (`keyphrases.py`): `1.0 / (yake_score + 0.2)` — raw YAKE
scores are lower-is-better; this inverts to higher-is-better while
avoiding divide-by-zero. Capped implicitly by YAKE's `top=30`.

Fuser (`fuse.py`):
1. Normalize label (`" ".join(strip().lower().split())`).
2. Sum weights across all sources sharing that label.
3. Sort desc, slice to `top_k`.
4. Normalize so the top entry's score is `1.0`.

## Dependencies

- `crawler.extractor.meta.MetaTags` — input type for heuristics.
- External: `yake`.

## Tests

`tests/test_classifier_heuristics.py`, `tests/test_classifier_keyphrases.py`,
`tests/test_classifier_fuse.py`. End-to-end ranking is exercised through
`tests/test_pipeline.py`.
