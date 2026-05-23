"""Headless worker: fetch via Playwright, then run the standard extraction pipeline."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from crawler.config import load_settings
from crawler.pipeline import process_html
from crawler.storage.dynamo import PagesRepo
from crawler.storage.s3 import RawHtmlStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _fetch_headless(url: str) -> tuple[str, int]:
    from playwright.async_api import async_playwright  # imported lazily

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.5 Safari/605.1.15"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            response = await page.goto(url, wait_until="networkidle", timeout=20000)
            html = await page.content()
            status = response.status if response else 200
            return html, status
        finally:
            await browser.close()


def handler(event: dict, context=None) -> dict:
    """Direct-invoke handler. Event: {url, [persist]}."""
    url = event["url"]
    persist = event.get("persist", True)
    html, status = asyncio.run(_fetch_headless(url))

    result = process_html(
        url=url, html=html, http_status=status,
        content_type="text/html", fetcher_used="headless",
    )

    if persist:
        s = load_settings()
        store = RawHtmlStore(bucket=s.raw_html_bucket)
        domain = urlsplit(url).netloc.lower()
        fetched_iso = result.fetched_at.isoformat()
        s3_html_uri = store.put_raw_html(
            url_hash=result.url_hash, domain=domain,
            fetched_at_iso=fetched_iso, html=html,
        )
        s3_jsonld_uri = (
            store.put_jsonld(
                url_hash=result.url_hash, domain=domain,
                fetched_at_iso=fetched_iso, jsonld=result.json_ld,
            ) if result.json_ld else None
        )
        PagesRepo(table_name=s.pages_table).put(
            result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri
        )

    return result.model_dump(mode="json")
