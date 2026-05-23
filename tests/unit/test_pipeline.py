"""Tests for the extract pipeline (no storage yet)."""
from unittest.mock import AsyncMock

import pytest

from crawler.fetcher.static import FetchResult
from crawler.pipeline import extract_pipeline


@pytest.mark.asyncio
async def test_pipeline_on_rei_fixture(fixture_html, monkeypatch):
    if "rei" not in fixture_html:
        pytest.skip("rei fixture not present")
    fake_fetch = AsyncMock(return_value=FetchResult(
        url="http://blog.rei.com/x",
        final_url="http://blog.rei.com/x",
        http_status=200,
        content_type="text/html",
        html=fixture_html["rei"],
    ))
    monkeypatch.setattr("crawler.pipeline.fetch", fake_fetch)

    result = await extract_pipeline("http://blog.rei.com/x")
    assert result.url == "http://blog.rei.com/x"
    assert result.http_status == 200
    assert result.title is not None and len(result.title) > 0
    assert result.body_text is not None
    assert result.word_count > 50
    assert len(result.topics) >= 3
    assert 0.0 <= result.extraction_confidence <= 1.0
    assert result.fetcher_used == "static"
