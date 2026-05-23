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
    cands = extract_keyphrases("too short", max_keyphrases=5)
    assert isinstance(cands, list)  # may be empty, must not crash


def test_extract_keyphrases_handles_none():
    cands = extract_keyphrases(None, max_keyphrases=5)
    assert cands == []
