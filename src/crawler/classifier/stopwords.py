"""Shared stopword set + filter for classifier signals.

Combines basic English function words with domain-specific web boilerplate
and citation noise. Used by both heuristics (title/h1/description tokens)
and keyphrases (YAKE post-filter).
"""
from __future__ import annotations

# Common English function words. Kept short — language-specific stopwords are
# handled inside YAKE via the per-language extractor.
_BASIC = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "your", "you",
    "are", "all", "but", "have", "has", "was", "were", "will", "would",
    "into", "than", "what", "when", "where", "which", "who",
    "how", "why", "https", "http", "www", "com", "html",
})

# HTML / UI / footer noise that consistently appears as YAKE keyphrases or
# title fragments but never represents a real topic.
_DOMAIN = frozenset({
    # navigation / UI boilerplate
    "click", "here", "more", "menu", "login", "logout", "sign", "share",
    "follow", "skip", "subscribe", "submit", "send", "search", "back",
    "next", "previous", "show", "hide", "open", "close", "view",
    # legal / footer
    "cookie", "cookies", "privacy", "terms", "policy", "policies",
    "rights", "reserved", "copyright",
    # academic / wiki citation noise
    "pmid", "doi", "isbn", "issn", "retrieved", "archived", "original",
    # generic time markers (article footers, dates)
    "yesterday", "today", "tomorrow",
})

STOPWORDS: frozenset[str] = _BASIC | _DOMAIN


def is_all_stopwords(label: str) -> bool:
    """True when every token in the label is a stopword (empty label included)."""
    tokens = [t for t in label.lower().split() if t]
    if not tokens:
        return True
    return all(t in STOPWORDS for t in tokens)
