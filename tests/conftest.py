"""Shared test fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html() -> dict[str, str]:
    """Return saved HTML for the three test URLs as a dict keyed by short name."""
    files = {
        "amazon": FIXTURES_DIR / "amazon_toaster.html",
        "rei": FIXTURES_DIR / "rei_outdoors.html",
        "cnn": FIXTURES_DIR / "cnn_tech.html",
    }
    return {key: path.read_text(encoding="utf-8") for key, path in files.items() if path.exists()}
