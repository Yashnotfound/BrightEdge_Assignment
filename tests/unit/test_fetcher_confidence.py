"""Tests for the extraction confidence scorer."""
from crawler.fetcher.confidence import score_confidence


def test_full_signals_yields_high_confidence():
    score = score_confidence(
        title="Some Title",
        body_word_count=500,
        has_structured_data=True,
        is_captcha=False,
    )
    assert score >= 0.9


def test_no_title_low_confidence():
    score = score_confidence(
        title=None, body_word_count=500, has_structured_data=True, is_captcha=False
    )
    assert score < 0.8


def test_captcha_caps_confidence():
    score = score_confidence(
        title="Robot Check", body_word_count=20, has_structured_data=False, is_captcha=True
    )
    assert score <= 0.2


def test_thin_body_reduces_confidence():
    score = score_confidence(
        title="X", body_word_count=10, has_structured_data=False, is_captcha=False
    )
    assert score < 0.5
