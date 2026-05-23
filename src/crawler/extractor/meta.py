"""Extract <title>, <meta>, OpenGraph, Twitter Card, canonical, keywords."""
from __future__ import annotations

from dataclasses import dataclass, field

from bs4 import BeautifulSoup


@dataclass
class MetaTags:
    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    keywords: list[str] = field(default_factory=list)
    open_graph: dict[str, str] = field(default_factory=dict)
    twitter_card: dict[str, str] = field(default_factory=dict)
    h1: list[str] = field(default_factory=list)
    raw_meta: dict[str, str] = field(default_factory=dict)


def extract_meta(html: str) -> MetaTags:
    soup = BeautifulSoup(html, "lxml")
    meta = MetaTags()

    if soup.title and soup.title.string:
        meta.title = soup.title.string.strip()

    for h1_tag in soup.find_all("h1", limit=3):
        text = h1_tag.get_text(" ", strip=True)
        if text:
            meta.h1.append(text)

    for tag in soup.find_all("meta"):
        name = (tag.get("name") or "").lower().strip()
        prop = (tag.get("property") or "").lower().strip()
        content = tag.get("content")
        if not content:
            continue
        content = content.strip()
        if name == "description":
            meta.description = content
        if name == "keywords":
            meta.keywords = [k.strip().lower() for k in content.split(",") if k.strip()]
        if prop.startswith("og:"):
            meta.open_graph[prop] = content
        if name.startswith("twitter:"):
            meta.twitter_card[name] = content
        if name:
            meta.raw_meta[name] = content

    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        meta.canonical_url = link["href"].strip()

    return meta
