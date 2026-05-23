"""Topic candidates from meta tags, OpenGraph, and JSON-LD."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crawler.extractor.meta import MetaTags

_WEIGHT_SCHEMA = 2.0  # schema.org categories (highest precision)
_WEIGHT_META_KEYWORD = 1.5
_WEIGHT_OG = 1.4
_WEIGHT_TITLE = 1.0


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


def candidates_from_meta_and_jsonld(
    meta: MetaTags, jsonld: list[dict[str, Any]]
) -> list[TopicCandidate]:
    out: list[TopicCandidate] = []

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
