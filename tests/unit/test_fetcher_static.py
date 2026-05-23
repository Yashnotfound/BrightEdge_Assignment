"""Tests for the static httpx fetcher."""
import httpx
import pytest
import respx

from crawler.fetcher.static import FetchResult, fetch


@pytest.mark.asyncio
async def test_fetch_returns_html_and_status():
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html><title>Hi</title></html>",
                                        headers={"content-type": "text/html"})
        )
        result = await fetch("https://example.com/")
        assert isinstance(result, FetchResult)
        assert result.http_status == 200
        assert "<title>Hi</title>" in result.html
        assert result.content_type.startswith("text/html")
        assert result.final_url == "https://example.com/"


@pytest.mark.asyncio
async def test_fetch_follows_redirects():
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/old").mock(
            return_value=httpx.Response(301, headers={"location": "https://example.com/new"})
        )
        router.get("https://example.com/new").mock(
            return_value=httpx.Response(
                200, text="redirected", headers={"content-type": "text/html"},
            )
        )
        result = await fetch("https://example.com/old")
        assert result.http_status == 200
        assert result.final_url == "https://example.com/new"
        assert result.html == "redirected"


@pytest.mark.asyncio
async def test_fetch_handles_non_html():
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/a.json").mock(
            return_value=httpx.Response(
                200, text='{"a":1}', headers={"content-type": "application/json"},
            )
        )
        result = await fetch("https://example.com/a.json")
        assert result.content_type == "application/json"
        assert result.html == '{"a":1}'
