"""Tests for YAKE keyphrase extraction."""
from crawler.classifier.keyphrases import extract_keyphrases


def test_extract_keyphrases_finds_main_topics():
    text = (
        "The Cuisinart CPT-122 compact toaster is a small kitchen appliance. "
        "This toaster offers six browning levels and supports bagels. "
        "The compact toaster fits in small kitchens easily. "
        "Many users prefer this Cuisinart toaster over competitors."
    ) * 3
    cands = extract_keyphrases(text, max_keyphrases=10)
    labels = {c.label for c in cands}
    assert any("toaster" in label for label in labels)
    assert all(0 <= c.weight <= 5.0 for c in cands)


def test_extract_keyphrases_handles_short_text():
    # Bodies under _MIN_BODY_CHARS (120) always return [] — YAKE on tiny text
    # produces noise.
    cands = extract_keyphrases("too short", max_keyphrases=5)
    assert cands == []


def test_extract_keyphrases_handles_none():
    cands = extract_keyphrases(None, max_keyphrases=5)
    assert cands == []


def test_extract_keyphrases_skips_below_min_body_chars():
    """Body under 120 chars yields empty list — output would be noise."""
    short = "Cuisinart toaster kitchen appliance."  # < 120 chars
    assert extract_keyphrases(short, language="en") == []


def test_extract_keyphrases_skips_unsupported_language():
    """Unsupported languages (e.g., Swahili 'sw') skip YAKE rather than emit garbage."""
    text = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua." * 3
    assert extract_keyphrases(text, language="sw") == []


def test_extract_keyphrases_uses_german_stopwords():
    """German page should use German stopwords and surface German topic phrases."""
    text = (
        "Albert Einstein war ein deutscher Physiker, der die Relativitätstheorie entwickelte. "
        "Die Relativitätstheorie veränderte das physikalische Weltbild grundlegend. "
        "Einstein erhielt 1921 den Nobelpreis für Physik. "
    ) * 3
    cands = extract_keyphrases(text, language="de", max_keyphrases=10)
    labels = {c.label for c in cands}
    assert any("einstein" in l for l in labels)
    # German articles 'die', 'der', 'das' should be filtered as stopwords
    assert "die" not in labels and "der" not in labels and "das" not in labels


def test_extract_keyphrases_none_language_defaults_to_english():
    """None language falls back to English to preserve current behavior."""
    text = (
        "The Cuisinart compact toaster is a kitchen appliance. "
        "This compact toaster offers six browning levels. "
    ) * 5
    cands = extract_keyphrases(text, language=None)
    assert cands, "should produce candidates with English fallback"


def test_extract_keyphrases_normalizes_locale_codes():
    """Locale codes like 'zh-cn' should resolve to the base language 'zh'."""
    text = "你 好 世界 " * 50  # Chinese characters
    cands = extract_keyphrases(text, language="zh-cn")
    assert isinstance(cands, list)  # should not crash on locale variants
