"""Client for invoking headless fetch — Lambda in prod, Playwright locally."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from crawler.config import load_settings

logger = logging.getLogger(__name__)


async def _fetch_headless_local(url: str) -> tuple[str, int]:
    """Run Playwright in-process to render a JS-heavy page."""
    from playwright.async_api import async_playwright  # lazy import

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
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
            response = await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            status = response.status if response else 200
            return html, status
        finally:
            await browser.close()


def invoke_headless_local(url: str) -> dict[str, Any]:
    """Fetch via local Playwright and run the extraction pipeline."""
    import concurrent.futures

    from crawler.pipeline import process_html  # avoid circular import

    logger.info("headless-local: fetching %s via Playwright", url)

    # Run Playwright in a separate thread because the caller may already be
    # inside an asyncio event loop (FastAPI).
    def _run() -> tuple[str, int]:
        return asyncio.run(_fetch_headless_local(url))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        html, status = pool.submit(_run).result(timeout=60)

    result = process_html(
        url=url, html=html, http_status=status,
        content_type="text/html", fetcher_used="headless",
    )
    return result.model_dump(mode="json")


def invoke_headless(url: str, *, persist: bool = False) -> dict[str, Any]:
    """Invoke headless fetch — routes to local Playwright or Lambda based on config."""
    s = load_settings()
    if not s.headless_function_name:
        raise RuntimeError("HEADLESS_FUNCTION_NAME not configured")

    if s.headless_function_name == "LOCAL":
        return invoke_headless_local(url)

    # Production path: invoke the Lambda function
    import boto3

    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=s.headless_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"url": url, "persist": persist}).encode("utf-8"),
    )
    payload = response["Payload"].read()
    return json.loads(payload)

