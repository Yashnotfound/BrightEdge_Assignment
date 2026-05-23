"""Extraction confidence scorer. Drives static→headless escalation."""
from __future__ import annotations


def score_confidence(
    *,
    title: str | None,
    body_word_count: int,
    has_structured_data: bool,
    is_captcha: bool,
) -> float:
    """Return confidence in [0.0, 1.0]. Threshold < 0.5 triggers headless retry."""
    if is_captcha:
        return 0.1

    score = 0.0
    if title and len(title.strip()) > 0:
        score += 0.3
    # Body bucket: 0 (<20), 0.2 (20-100), 0.3 (100-300), 0.4 (>=300)
    if body_word_count >= 300:
        score += 0.4
    elif body_word_count >= 100:
        score += 0.3
    elif body_word_count >= 20:
        score += 0.2
    if has_structured_data:
        score += 0.2
    score += 0.1  # baseline "not blocked"
    return round(min(1.0, score), 3)


def is_likely_captcha(title: str | None, body: str | None) -> bool:
    """Cheap fingerprint check for common anti-bot pages."""
    needles = ("robot check", "are you a robot", "captcha", "human verification")
    haystack = " ".join(filter(None, [title or "", (body or "")[:500]])).lower()
    return any(n in haystack for n in needles)
