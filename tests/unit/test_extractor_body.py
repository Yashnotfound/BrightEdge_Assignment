"""Tests for body text extraction."""
from crawler.extractor.body import extract_body


def test_extract_body_strips_boilerplate():
    html = """
    <html><body>
        <nav>Home | About | Contact</nav>
        <article>
            <h1>Main Title</h1>
            <p>This is the main content paragraph with substantive text about the topic.</p>
            <p>Another paragraph with more details that should be extracted.</p>
        </article>
        <footer>Copyright 2025</footer>
    </body></html>
    """
    text = extract_body(html)
    assert text is not None
    assert "main content paragraph" in text.lower()
    assert "copyright" not in text.lower()  # boilerplate stripped


def test_extract_body_handles_minimal_html():
    html = "<html><body><p>Tiny</p></body></html>"
    text = extract_body(html)
    # trafilatura may return None for very short content; that's OK
    assert text is None or "Tiny" in text


def test_extract_body_word_count():
    html = "<html><body><article><p>" + ("word " * 200) + "</p></article></body></html>"
    text = extract_body(html)
    assert text is not None
    assert len(text.split()) >= 100
