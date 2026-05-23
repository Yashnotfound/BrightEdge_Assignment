"""Body text extraction using trafilatura."""
from __future__ import annotations

import trafilatura


def extract_body(html: str) -> str | None:
    """Return main content text (boilerplate-stripped) or None if too sparse."""
    return trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
        no_fallback=False,
    )
