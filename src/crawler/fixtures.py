"""Hard-coded fallback responses for the demo when live fetch is impossible.

Only used when `?fixture=1` is passed. The response is clearly labeled in the
output so reviewers see exactly what happened.
"""
from __future__ import annotations

from datetime import UTC, datetime

from crawler.api.schemas import ExtractResult, Topic
from crawler.storage.hashing import url_hash

_AMAZON_URL = "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"
_REI_URL = "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"
_CNN_URL = "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"

_FIXTURE_ERROR = (
    "fixture_mode: served from stored response, live fetch blocked from Lambda egress IPs"
)


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
        errors=[_FIXTURE_ERROR],
    )


def rei_outdoors() -> ExtractResult:
    return ExtractResult(
        url=_REI_URL,
        url_hash=url_hash(_REI_URL),
        fetched_at=datetime.now(UTC),
        fetcher_used="fixture",
        http_status=200,
        content_type="text/html",
        language="en",
        title=(
            "How to Introduce Your Indoorsy Friend to the Outdoors"
            " - Uncommon Path – An REI Co-op Publication"
        ),
        description=(
            "Share your passion for the outdoors and introduce a friend to hike and camp"
            "—don't forget to start slow."
        ),
        canonical_url=_REI_URL,
        open_graph={
            "og:type": "article",
            "og:title": "How to Introduce Your Indoorsy Friend to the Outdoors",
            "og:description": (
                "Got a friend who'd rather stay inside? Here's how to ease them into"
                " enjoying the great outdoors."
            ),
            "og:url": _REI_URL,
            "og:site_name": "REI Co-op Journal",
            "article:section": "camping",
        },
        json_ld=[{
            "@type": "BlogPosting",
            "headline": "How to Introduce Your Indoorsy Friend to the Outdoors",
            "description": (
                "Share your passion for the outdoors and introduce a friend to hike and camp."
            ),
            "articleSection": "Camping",
            "publisher": {"@type": "Organization", "name": "REI Co-op"},
        }],
        body_text=(
            "Getting a friend outside for the first time can be tricky. Start with a short"
            " day hike, pack extra snacks, and choose a beginner-friendly trail. Make it"
            " social, not a fitness test. Let them set the pace. Camping overnight can"
            " follow once they're comfortable with day trips."
        ),
        word_count=52,
        topics=[
            Topic(label="camping", score=1.0,
                  sources=["og:article:section", "jsonld:articleSection"]),
            Topic(label="outdoors", score=0.85, sources=["title", "og:title"]),
            Topic(label="introduce friend outdoors", score=0.7, sources=["title", "yake"]),
            Topic(label="hiking", score=0.55, sources=["body", "yake"]),
            Topic(label="beginner", score=0.45, sources=["body", "yake"]),
        ],
        extraction_confidence=0.95,
        errors=[_FIXTURE_ERROR],
    )


def cnn_tech() -> ExtractResult:
    return ExtractResult(
        url=_CNN_URL,
        url_hash=url_hash(_CNN_URL),
        fetched_at=datetime.now(UTC),
        fetcher_used="fixture",
        http_status=200,
        content_type="text/html",
        language="en",
        title="Google study reveals AI is impacting 90 percent of tech jobs | CNN Business",
        description=(
            "A new Google study finds that nearly 90 percent of tech roles are now being"
            " impacted by AI tools."
        ),
        canonical_url=_CNN_URL,
        open_graph={
            "og:type": "article",
            "og:title": (
                "Google study reveals AI is impacting 90 percent of tech jobs | CNN Business"
            ),
            "og:description": (
                "The overwhelming majority of tech industry workers use artificial intelligence"
                " on the job for tasks like writing and modifying code."
            ),
            "og:url": _CNN_URL,
            "og:site_name": "CNN",
            "article:section": "tech",
            "article:tag": "ai",
        },
        json_ld=[{
            "@type": "NewsArticle",
            "headline": "Google study reveals AI is impacting 90 percent of tech jobs",
            "articleSection": "tech",
            "keywords": "AI, tech jobs, Google, automation, developers, artificial intelligence",
            "publisher": {"@type": "Organization", "name": "CNN"},
        }],
        body_text=(
            "A new Google study finds that nearly 90 percent of tech roles are now being"
            " impacted by AI tools. Engineers, developers, and data scientists report using"
            " AI for code generation, debugging, and documentation. The research highlights"
            " accelerating automation across the tech industry."
        ),
        word_count=46,
        topics=[
            Topic(label="ai", score=1.0, sources=["og:article:tag", "jsonld:keywords", "title"]),
            Topic(label="tech jobs", score=0.85, sources=["title", "jsonld:keywords"]),
            Topic(label="google", score=0.7, sources=["title", "body"]),
            Topic(label="automation", score=0.55, sources=["jsonld:keywords", "body"]),
            Topic(label="developers", score=0.45, sources=["jsonld:keywords", "body"]),
        ],
        extraction_confidence=0.95,
        errors=[_FIXTURE_ERROR],
    )
