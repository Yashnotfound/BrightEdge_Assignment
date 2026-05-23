"""Extract JSON-LD structured data blocks."""
from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup


def extract_jsonld(html: str) -> list[dict[str, Any]]:
    """Return all valid JSON-LD blocks as a flat list of dicts."""
    soup = BeautifulSoup(html, "lxml")
    blocks: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = (script.string or script.get_text() or "").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    blocks.append(item)
        elif isinstance(parsed, dict):
            blocks.append(parsed)
    return blocks
