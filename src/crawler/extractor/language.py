"""Language detection wrapper around langdetect (with seed for determinism)."""
from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0


def detect_language(text: str | None) -> str | None:
    if not text or len(text.strip()) < 20:
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None
