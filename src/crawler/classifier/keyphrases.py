"""Keyphrase candidates via YAKE, with per-language extractors."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import yake

from crawler.classifier.stopwords import is_all_stopwords


@dataclass(frozen=True)
class KeyphraseCandidate:
    label: str
    weight: float  # higher = more important


# Languages with YAKE-bundled stopword lists (yake/StopwordsList/*.txt).
_YAKE_SUPPORTED_LANGS = frozenset({
    "ar", "bg", "br", "cs", "da", "de", "el", "en", "es", "et",
    "fa", "fi", "fr", "hi", "hr", "hu", "hy", "id", "it", "ja",
    "lt", "lv", "nl", "no", "pl", "pt", "ro", "ru", "sk", "sl",
    "sv", "tr", "uk", "zh",
})

# langdetect -> YAKE language code overrides. Currently only Czech differs:
# YAKE ships its stopword file as `stopwords_cz.txt`, not `stopwords_cs.txt`.
# Add more entries here only if a future YAKE release uses a non-ISO 639-1 code.
_YAKE_LANG_OVERRIDES = {"cs": "cz"}

# Skip YAKE on extremely short bodies — output is noise and the work is wasted.
_MIN_BODY_CHARS = 120


@lru_cache(maxsize=len(_YAKE_SUPPORTED_LANGS))
def _get_extractor(lang: str) -> yake.KeywordExtractor:
    """Cache one YAKE extractor per language (constructor reads a stopword file)."""
    yake_lang = _YAKE_LANG_OVERRIDES.get(lang, lang)
    return yake.KeywordExtractor(lan=yake_lang, n=3, dedupLim=0.8, top=30)


def _normalize_lang(language: str | None) -> str | None:
    """Map a langdetect code (e.g., 'en', 'zh-cn') to a YAKE-supported code, or None."""
    if not language:
        return None
    code = language.lower().split("-")[0]
    return code if code in _YAKE_SUPPORTED_LANGS else None


def extract_keyphrases(
    text: str | None,
    *,
    language: str | None = "en",
    max_keyphrases: int = 20,
) -> list[KeyphraseCandidate]:
    """Return YAKE keyphrases ranked by inverted YAKE score.

    Returns [] when the body is too short or the language is unsupported —
    cleaner than running YAKE in English mode on, say, Japanese text.
    """
    if not text or len(text.strip()) < _MIN_BODY_CHARS:
        return []
    if language is None:
        yake_lang = "en"  # langdetect failed — assume English (the common case)
    else:
        yake_lang = _normalize_lang(language)
        if yake_lang is None:
            return []  # explicit unsupported language — skip rather than emit garbage
    raw = _get_extractor(yake_lang).extract_keywords(text)
    # YAKE scores: lower = more important. Invert with `1/(score+0.2)` so the
    # most informative keyphrases land near weight 5.0 and the formula is
    # smooth across the score range (avoids saturation; see commit 6aac7a6).
    out: list[KeyphraseCandidate] = []
    for phrase, score in raw[:max_keyphrases]:
        label = phrase.lower()
        # Drop keyphrases that consist entirely of stopwords / web boilerplate
        # (e.g., "click here", "read more", "pmid"). The per-language YAKE
        # stopword list catches function words; this catches domain noise.
        if is_all_stopwords(label):
            continue
        weight = 1.0 / (score + 0.2)
        out.append(KeyphraseCandidate(label=label, weight=weight))
    return out
