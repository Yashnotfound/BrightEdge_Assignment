"""Headless worker: fetch via Playwright, then run the standard extraction pipeline."""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit

from crawler.api.schemas import ExtractResult
from crawler.config import load_settings
from crawler.fetcher.url_safety import UnsafeUrlError, validate_url
from crawler.persist_gate import reject_reason, to_rejected
from crawler.pipeline import process_html
from crawler.storage.dynamo import PagesRepo
from crawler.storage.s3 import RawHtmlStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _fetch_headless(url: str) -> tuple[str, int]:
    # SSRF guard — defense in depth. The API layer (Pydantic) should have
    # validated, and the static worker also validates before invoking us,
    # but a direct invoke (or future code path) could bypass both. Raise
    # `UnsafeUrlError` BEFORE booting Chromium so we don't waste a few
    # seconds spinning up a browser just to block the navigation.
    validate_url(url)

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
            # SSRF guard for in-page navigations and sub-resource loads.
            # Playwright follows redirects internally — without intercepting
            # at the request layer, a 302 to 169.254.169.254 would bypass
            # the entry-point `validate_url` call. The handler also blocks
            # malicious sub-resources (an `<img src="http://10.0.0.1/...">`
            # would otherwise leak that we visited the page).
            async def _block_unsafe(route, request):
                try:
                    validate_url(request.url)
                except UnsafeUrlError:
                    await route.abort("blockedbyclient")
                    return
                await route.continue_()
            await context.route("**/*", _block_unsafe)
            page = await context.new_page()
            # `networkidle` waits for the network to be quiet for ~500ms — the right
            # primitive for SPAs (React/Vue/etc.) whose content paints AFTER initial
            # DOM parse. Bounded by `timeout` so a page with long-polling websockets
            # can't hang the worker.
            response = await page.goto(url, wait_until="networkidle", timeout=15000)
            # Even after networkidle, give the framework one more beat to actually
            # render text into the body. Caps headless wait at ~18s total worst-case;
            # Lambda timeout is 60s so we still have plenty of headroom for parse +
            # classify downstream.
            try:
                await page.wait_for_function(
                    "() => document.body && document.body.innerText.trim().length > 200",
                    timeout=3000,
                )
            except Exception:  # noqa: BLE001, S110 - thin SPA / auth wall / static page — accept what we have
                pass
            html = await page.content()
            status = response.status if response else 200
            return html, status
        finally:
            await browser.close()


async def _persist_headless(result: ExtractResult, html: str | None) -> ExtractResult:
    """Mirror of routes._persist for the headless worker: parallel S3 writes,
    then a DynamoDB write that references the resulting URIs.

    Runs the persist gate first: a 4xx/5xx/captcha response is replaced with
    a rejected marker, the S3 raw-HTML write is skipped, and the DDB row is
    still written so /pages keeps the audit trail. Returns the result that
    was actually persisted (may differ from input).

    `html=None` is the skip-S3 sentinel (matches `routes._persist` style); an
    empty string `""` is a legitimately-empty body that still gets written
    so the row's `s3_html_uri` reflects what was fetched.
    """
    reason = reject_reason(result, html)
    if reason is not None:
        result = to_rejected(result, reason)
        html = None  # sentinel: "skip the S3 raw-HTML write" — distinct from ""

    s = load_settings()
    if not s.raw_html_bucket or not s.pages_table:
        return result  # local-dev fallback: skip persistence (matches routes._persist)
    store = RawHtmlStore(bucket=s.raw_html_bucket)
    domain = urlsplit(result.url).netloc.lower()
    fetched_iso = result.fetched_at.isoformat()

    html_task = (
        asyncio.to_thread(
            store.put_raw_html,
            url_hash=result.url_hash, domain=domain,
            fetched_at_iso=fetched_iso, html=html,
        )
        if html is not None
        else None
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

    if html_task is not None and jsonld_task is not None:
        s3_html_uri, s3_jsonld_uri = await asyncio.gather(html_task, jsonld_task)
    elif html_task is not None:
        s3_html_uri = await html_task
        s3_jsonld_uri = None
    elif jsonld_task is not None:
        s3_html_uri = None
        s3_jsonld_uri = await jsonld_task
    else:
        s3_html_uri = None
        s3_jsonld_uri = None

    await asyncio.to_thread(
        PagesRepo(table_name=s.pages_table).put,
        result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri,
    )
    return result


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
        # _persist_headless may swap `result` for a rejected marker if the
        # persist gate fires; reflect that in the handler return value so
        # callers (e.g. routes.extract → invoke_headless) see the rejection.
        result = asyncio.run(_persist_headless(result, html))

    return result.model_dump(mode="json")
