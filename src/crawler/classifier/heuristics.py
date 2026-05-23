"""Topic candidates from meta tags, OpenGraph, and JSON-LD."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crawler.classifier.stopwords import STOPWORDS, is_all_stopwords
from crawler.extractor.meta import MetaTags

_WEIGHT_SCHEMA = 2.0  # schema.org categories (highest precision)
_WEIGHT_META_KEYWORD = 1.5
_WEIGHT_OG = 1.4
_WEIGHT_TITLE_PHRASE = 1.5
_WEIGHT_TITLE_TOKEN = 1.0
_WEIGHT_H1_PHRASE = 1.3
_WEIGHT_H1_TOKEN = 0.9
_WEIGHT_DESCRIPTION_PHRASE = 1.0
_WEIGHT_DESCRIPTION_TOKEN = 0.6

# Characters that commonly separate distinct topic chunks within a title or
# heading (e.g., "Site | Article", "Title: Subtitle", "Article — Site").
_TITLE_SPLIT_CHARS = ":|—–•·"


@dataclass(frozen=True)
class TopicCandidate:
    label: str  # lowercased
    weight: float
    source: str


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _split_category(text: str) -> list[str]:
    """Split a schema.org-style category breadcrumb into parts."""
    parts = []
    for chunk in text.replace(">", "|").split("|"):
        chunk = _norm(chunk)
        if chunk:
            parts.append(chunk)
    return parts


def _split_title_chunks(text: str) -> list[str]:
    """Split a title/h1/description on common separators into candidate phrases."""
    s = text
    for ch in _TITLE_SPLIT_CHARS:
        s = s.replace(ch, "|")
    s = s.replace("(", "|").replace(")", "|").replace("[", "|").replace("]", "|")
    chunks = [_norm(c) for c in s.split("|")]
    return [c for c in chunks if c]


def _content_tokens(chunk: str) -> list[str]:
    """Tokenize a chunk into content words (alphanumeric, >= 4 chars, non-stopword)."""
    out: list[str] = []
    for raw in chunk.split():
        # strip surrounding punctuation but keep internal hyphens
        t = raw.strip(".,;!?\"'`").lower()
        if not t:
            continue
        # require at least one alpha character to skip pure-digit tokens
        if not any(c.isalpha() for c in t):
            continue
        if len(t) >= 4 and t not in STOPWORDS:
            out.append(t)
    return out


def _text_signal_candidates(
    text: str | None,
    phrase_weight: float,
    token_weight: float,
    source: str,
) -> list[TopicCandidate]:
    """Emit phrase + token candidates from a title / h1 / description string."""
    if not text:
        return []
    out: list[TopicCandidate] = []
    for chunk in _split_title_chunks(text):
        # phrase candidate: only if it looks like a topic (4-60 chars, has alpha,
        # and not made entirely of stopwords)
        if (
            4 <= len(chunk) <= 60
            and any(c.isalpha() for c in chunk)
            and not is_all_stopwords(chunk)
        ):
            out.append(TopicCandidate(chunk, phrase_weight, source))
        for tok in _content_tokens(chunk):
            out.append(TopicCandidate(tok, token_weight, source))
    return out


def candidates_from_meta_and_jsonld(
    meta: MetaTags, jsonld: list[dict[str, Any]]
) -> list[TopicCandidate]:
    out: list[TopicCandidate] = []

    # title — single biggest under-used signal: phrase chunks + content tokens
    out.extend(_text_signal_candidates(
        meta.title, _WEIGHT_TITLE_PHRASE, _WEIGHT_TITLE_TOKEN, "title",
    ))

    # h1 — typically restates page topic; up to 3 retained by extractor
    for h1 in meta.h1:
        out.extend(_text_signal_candidates(
            h1, _WEIGHT_H1_PHRASE, _WEIGHT_H1_TOKEN, "h1",
        ))

    # meta description — full sentence; useful but noisier than title
    out.extend(_text_signal_candidates(
        meta.description, _WEIGHT_DESCRIPTION_PHRASE, _WEIGHT_DESCRIPTION_TOKEN,
        "description",
    ))

    # meta keywords
    for kw in meta.keywords:
        label = _norm(kw)
        if label:
            out.append(TopicCandidate(label, _WEIGHT_META_KEYWORD, "meta:keywords"))

    # OpenGraph type & product/article tags
    for og_key, og_val in meta.open_graph.items():
        if og_key in {"og:type"}:
            label = _norm(og_val)
            if label:
                out.append(TopicCandidate(label, _WEIGHT_OG, og_key))
        elif og_key in {"product:category", "article:section"}:
            for part in _split_category(og_val):
                out.append(TopicCandidate(part, _WEIGHT_OG, og_key))
        elif og_key.startswith("article:tag"):
            label = _norm(og_val)
            if label:
                out.append(TopicCandidate(label, _WEIGHT_OG, og_key))

    # JSON-LD
    for block in jsonld:
        cat = block.get("category")
        if isinstance(cat, str):
            for part in _split_category(cat):
                out.append(TopicCandidate(part, _WEIGHT_SCHEMA, "jsonld:category"))
        kws = block.get("keywords")
        if isinstance(kws, list):
            for k in kws:
                if isinstance(k, str):
                    label = _norm(k)
                    if label:
                        out.append(TopicCandidate(label, _WEIGHT_SCHEMA, "jsonld:keywords"))
        elif isinstance(kws, str):
            for k in kws.split(","):
                label = _norm(k)
                if label:
                    out.append(TopicCandidate(label, _WEIGHT_SCHEMA, "jsonld:keywords"))
        t = block.get("@type")
        if isinstance(t, str):
            label = _norm(t)
            if label:
                out.append(TopicCandidate(label, _WEIGHT_SCHEMA * 0.5, "jsonld:type"))

    return out
