"""Headless worker: fetch via Playwright, then run the standard extraction pipeline."""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit

from crawler.api.schemas import ExtractResult
from crawler.config import load_settings
from crawler.pipeline import process_html
from crawler.storage.dynamo import PagesRepo
from crawler.storage.s3 import RawHtmlStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _fetch_headless(url: str) -> tuple[str, int]:
    from playwright.async_api import async_playwright  # imported lazily

    # Sparticuz/chromium recommended args. The longer list disables features
    # that don't make sense in a headless Lambda environment (background sync,
    # WebGL, audio, GPU). Without these, chromium child processes crash on
    # startup with TargetClosedError.
    chromium_args = [
        "--allow-pre-commit-input",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-component-extensions-with-background-pages",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
        "--disable-hang-monitor",
        "--disable-ipc-flooding-protection",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-renderer-backgrounding",
        "--disable-sync",
        "--force-color-profile=srgb",
        "--metrics-recording-only",
        "--no-first-run",
        "--no-sandbox",
        "--no-default-browser-check",
        "--no-zygote",
        "--password-store=basic",
        "--use-mock-keychain",
        "--hide-scrollbars",
        "--mute-audio",
        "--headless=new",
    ]

    executable = os.environ.get("CHROMIUM_EXECUTABLE")
    if not executable:
        # The headless image bakes this env var via Dockerfile ENV directive;
        # missing it means we'd silently fall back to playwright's bundled
        # browser (which isn't installed) and crash deep inside launch().
        raise RuntimeError("CHROMIUM_EXECUTABLE env var is required")

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=executable, args=chromium_args)
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
            response = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            html = await page.content()
            status = response.status if response else 200
            return html, status
        finally:
            await browser.close()


async def _persist_headless(result: ExtractResult, html: str) -> None:
    """Mirror of routes._persist for the headless worker: parallel S3 writes,
    then a DynamoDB write that references the resulting URIs."""
    s = load_settings()
    if not s.raw_html_bucket or not s.pages_table:
        return  # local-dev fallback: skip persistence (matches routes._persist)
    store = RawHtmlStore(bucket=s.raw_html_bucket)
    domain = urlsplit(result.url).netloc.lower()
    fetched_iso = result.fetched_at.isoformat()

    html_task = asyncio.to_thread(
        store.put_raw_html,
        url_hash=result.url_hash, domain=domain,
        fetched_at_iso=fetched_iso, html=html,
    )
    jsonld_task = (
        asyncio.to_thread(
            store.put_jsonld,
            url_hash=result.url_hash, domain=domain,
            fetched_at_iso=fetched_iso, jsonld=result.json_ld,
        )
        if result.json_ld
        else None
    )

    if jsonld_task is not None:
        s3_html_uri, s3_jsonld_uri = await asyncio.gather(html_task, jsonld_task)
    else:
        s3_html_uri = await html_task
        s3_jsonld_uri = None

    await asyncio.to_thread(
        PagesRepo(table_name=s.pages_table).put,
        result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri,
    )


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
        asyncio.run(_persist_headless(result, html))

    return result.model_dump(mode="json")
