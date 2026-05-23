"""Tests for language detection."""
from crawler.extractor.language import detect_language


def test_detect_english():
    text = "This is a sample English text used to test language detection. " * 5
    assert detect_language(text) == "en"


def test_detect_returns_none_for_empty():
    assert detect_language("") is None
    assert detect_language(None) is None
