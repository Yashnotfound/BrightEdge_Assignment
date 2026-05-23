"""Hard-coded fallback responses for the demo when live fetch is impossible.

Only used when `?fixture=1` is passed. The response is clearly labeled in the
output so reviewers see exactly what happened.
"""
from __future__ import annotations

from datetime import UTC, datetime

from crawler.api.schemas import ExtractResult, Topic
from crawler.storage.hashing import url_hash

_AMAZON_URL = "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"


def amazon_toaster() -> ExtractResult:
    return ExtractResult(
        url=_AMAZON_URL,
        url_hash=url_hash(_AMAZON_URL),
        fetched_at=datetime.now(UTC),
        fetcher_used="fixture",
        http_status=200,
        content_type="text/html",
        language="en",
        title="Cuisinart CPT-122 Compact 2-Slice Toaster",
        description=(
            "Compact 2-slice toaster with 6 browning levels, defrost, "
            "reheat, and bagel function."
        ),
        canonical_url=_AMAZON_URL,
        open_graph={"og:type": "product", "product:category": "kitchen toasters"},
        json_ld=[{
            "@type": "Product",
            "name": "Cuisinart CPT-122 Compact 2-Slice Toaster",
            "category": "Kitchen > Small Appliances > Toasters",
            "brand": {"@type": "Brand", "name": "Cuisinart"},
        }],
        body_text=(
            "Cuisinart CPT-122 compact 2-slice toaster. 6 browning levels. "
            "Defrost, reheat, bagel function. Removable crumb tray. Stainless steel housing."
        ),
        word_count=24,
        topics=[
            Topic(label="toaster", score=1.0, sources=["meta:keywords", "og:product:category"]),
            Topic(label="cuisinart", score=0.85, sources=["jsonld:brand", "title"]),
            Topic(label="kitchen", score=0.7, sources=["jsonld:category", "og:product:category"]),
            Topic(label="small appliances", score=0.55, sources=["jsonld:category"]),
            Topic(label="compact 2-slice", score=0.45, sources=["title", "yake"]),
        ],
        extraction_confidence=0.95,
        errors=["fixture_mode: served from stored response, live fetch unavailable due to anti-bot"],
    )
