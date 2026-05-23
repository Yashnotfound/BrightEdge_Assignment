"""Tests for JSON-LD extraction."""
from crawler.extractor.jsonld import extract_jsonld


def test_extract_single_jsonld_block():
    html = '''
    <html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"X"}
    </script></head></html>
    '''
    blocks = extract_jsonld(html)
    assert len(blocks) == 1
    assert blocks[0]["@type"] == "Product"
    assert blocks[0]["name"] == "X"


def test_extract_multiple_jsonld_blocks():
    html = '''
    <html><head>
    <script type="application/ld+json">{"@type":"Article","headline":"A"}</script>
    <script type="application/ld+json">{"@type":"BreadcrumbList"}</script>
    </head></html>
    '''
    blocks = extract_jsonld(html)
    assert len(blocks) == 2


def test_extract_jsonld_array():
    html = '''
    <html><head><script type="application/ld+json">
    [{"@type":"Product","name":"A"},{"@type":"Product","name":"B"}]
    </script></head></html>
    '''
    blocks = extract_jsonld(html)
    assert len(blocks) == 2
    assert {b["name"] for b in blocks} == {"A", "B"}


def test_extract_jsonld_skips_malformed():
    html = '''
    <html><head>
    <script type="application/ld+json">{not json}</script>
    <script type="application/ld+json">{"@type":"Article"}</script>
    </head></html>
    '''
    blocks = extract_jsonld(html)
    assert len(blocks) == 1
    assert blocks[0]["@type"] == "Article"
