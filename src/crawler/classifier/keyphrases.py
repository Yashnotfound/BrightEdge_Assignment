"""Keyphrase candidates via YAKE."""
from __future__ import annotations

from dataclasses import dataclass

import yake


@dataclass(frozen=True)
class KeyphraseCandidate:
    label: str
    weight: float  # higher = more important


_EXTRACTOR = yake.KeywordExtractor(
    lan="en",
    n=3,         # up to 3-grams
    dedupLim=0.8,
    top=30,
)


def extract_keyphrases(text: str | None, *, max_keyphrases: int = 20) -> list[KeyphraseCandidate]:
    if not text or len(text.strip()) < 50:
        return []
    raw = _EXTRACTOR.extract_keywords(text)
    # YAKE scores: lower = more important. Invert so larger = better topic.
    out: list[KeyphraseCandidate] = []
    for phrase, score in raw[:max_keyphrases]:
        # Map YAKE score (typically 0.0–0.5) to weight via inverse
        # Floor to avoid div-by-zero; cap at 5.0
        weight = min(5.0, 1.0 / max(score, 0.01))
        out.append(KeyphraseCandidate(label=phrase.lower(), weight=weight))
    return out
