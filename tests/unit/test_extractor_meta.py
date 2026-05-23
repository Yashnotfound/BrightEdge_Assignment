"""Tests for HTML meta extraction."""
from crawler.extractor.meta import extract_meta


def test_extract_title():
    html = "<html><head><title>Hello</title></head></html>"
    assert extract_meta(html).title == "Hello"


def test_extract_meta_description():
    html = '<html><head><meta name="description" content="Desc"></head></html>'
    assert extract_meta(html).description == "Desc"


def test_extract_og_tags():
    html = """<html><head>
        <meta property="og:title" content="OG Title">
        <meta property="og:type" content="product">
        <meta property="og:image" content="https://example.com/img.jpg">
    </head></html>"""
    meta = extract_meta(html)
    assert meta.open_graph["og:title"] == "OG Title"
    assert meta.open_graph["og:type"] == "product"


def test_extract_twitter_card():
    html = """<html><head>
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:title" content="Tweet">
    </head></html>"""
    meta = extract_meta(html)
    assert meta.twitter_card["twitter:card"] == "summary_large_image"


def test_extract_canonical_url():
    html = '<html><head><link rel="canonical" href="https://example.com/x"></head></html>'
    assert extract_meta(html).canonical_url == "https://example.com/x"


def test_extract_meta_keywords():
    html = '<html><head><meta name="keywords" content="toaster, kitchen, cuisinart"></head></html>'
    meta = extract_meta(html)
    assert meta.keywords == ["toaster", "kitchen", "cuisinart"]


def test_handles_missing_head():
    html = "<html><body>No head</body></html>"
    meta = extract_meta(html)
    assert meta.title is None
    assert meta.description is None


def test_extract_h1_single():
    html = "<html><body><h1>Main Heading</h1></body></html>"
    assert extract_meta(html).h1 == ["Main Heading"]


def test_extract_h1_strips_nested_markup():
    html = "<html><body><h1>Top <span>level</span> <strong>title</strong></h1></body></html>"
    assert extract_meta(html).h1 == ["Top level title"]


def test_extract_h1_limit_three():
    html = "<html><body>" + "".join(f"<h1>H{i}</h1>" for i in range(5)) + "</body></html>"
    assert extract_meta(html).h1 == ["H0", "H1", "H2"]


def test_extract_h1_empty_when_absent():
    html = "<html><body><p>No heading</p></body></html>"
    assert extract_meta(html).h1 == []


def test_rei_fixture_extracts_title(fixture_html):
    if "rei" not in fixture_html:
        return  # fixture optional in CI
    meta = extract_meta(fixture_html["rei"])
    assert meta.title is not None
    assert len(meta.title) > 0
