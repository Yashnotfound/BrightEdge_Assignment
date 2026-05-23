"""Tests for the config module."""
import os

from crawler.config import load_settings


def test_default_settings_have_table_names():
    # Clear env to test pure defaults
    for var in ["PAGES_TABLE", "JOBS_TABLE", "RAW_HTML_BUCKET", "JOBS_BUCKET",
                "STATIC_QUEUE_URL", "HEADLESS_FUNCTION_NAME"]:
        os.environ.pop(var, None)
    s = load_settings()
    assert s.pages_table == "brightedge-pages"
    assert s.jobs_table == "brightedge-jobs"
    assert s.confidence_threshold == 0.5
    assert s.raw_html_bucket == ""  # no default — must be set at runtime


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("PAGES_TABLE", "custom-pages")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.7")
    s = load_settings()
    assert s.pages_table == "custom-pages"
    assert s.confidence_threshold == 0.7
