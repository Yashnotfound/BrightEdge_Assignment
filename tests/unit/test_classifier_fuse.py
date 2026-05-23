"""Tests for topic fusion."""
# Intentional private imports: _merge_near_duplicates and _token_set_overlap
# are the dedup primitives, exercised directly so behavior changes show up as
# unit failures rather than only as end-to-end accuracy regressions.
from crawler.classifier.fuse import (
    Topic,
    _merge_near_duplicates,
    _token_set_overlap,
    fuse_topics,
)
from crawler.classifier.heuristics import TopicCandidate
from crawler.classifier.keyphrases import KeyphraseCandidate


def test_fuse_merges_duplicate_labels():
    heuristic = [
        TopicCandidate("toaster", 1.5, "meta:keywords"),
        TopicCandidate("toaster", 2.0, "jsonld:category"),
    ]
    keyphrase = [KeyphraseCandidate("toaster", 3.0)]
    topics = fuse_topics(heuristic, keyphrase, top_k=5)
    toaster = next(t for t in topics if t.label == "toaster")
    # All three signals merged into one topic; with only one topic, normalized score is 1.0
    assert toaster.score == 1.0
    assert len(toaster.sources) >= 2  # at least two distinct source strings


def test_fuse_returns_top_k_by_score():
    heuristic = [
        TopicCandidate(f"topic_{i}", float(i), "meta:keywords") for i in range(1, 21)
    ]
    topics = fuse_topics(heuristic, [], top_k=5)
    assert len(topics) == 5
    assert topics[0].score >= topics[-1].score


def test_fuse_dedupes_case_insensitive():
    heuristic = [
        TopicCandidate("Toaster", 1.5, "meta:keywords"),
        TopicCandidate("toaster", 1.0, "og:type"),
    ]
    topics = fuse_topics(heuristic, [], top_k=5)
    assert len([t for t in topics if t.label.lower() == "toaster"]) == 1


def test_fuse_normalizes_scores_to_0_1():
    heuristic = [TopicCandidate(f"t_{i}", float(i), "meta:keywords") for i in range(1, 11)]
    topics = fuse_topics(heuristic, [], top_k=10)
    assert all(0.0 <= t.score <= 1.0 for t in topics)
    assert topics[0].score == 1.0


def test_token_set_overlap_exact_subset():
    assert _token_set_overlap("python", "python software foundation") == 1.0
    assert _token_set_overlap("neural networks", "artificial neural networks") == 1.0


def test_token_set_overlap_partial_and_disjoint():
    assert _token_set_overlap("climate change", "global warming") == 0.0
    assert _token_set_overlap("machine learning", "deep learning") == 0.5


def test_merge_near_duplicates_collapses_subset_phrases():
    sorted_topics = [
        Topic(label="python", score=3.0, sources=["yake"]),
        Topic(label="python software foundation", score=1.5, sources=["yake"]),
        Topic(label="python enhancement proposals", score=1.2, sources=["yake"]),
        Topic(label="programming", score=1.0, sources=["meta:keywords"]),
    ]
    out = _merge_near_duplicates(sorted_topics)
    labels = [t.label for t in out]
    # "python" absorbs both subset phrases
    assert labels == ["python", "programming"]
    python = next(t for t in out if t.label == "python")
    assert python.score == 3.0 + 1.5 + 1.2


def test_merge_keeps_disjoint_topics():
    sorted_topics = [
        Topic(label="kitchen", score=2.0, sources=["jsonld:category"]),
        Topic(label="toaster", score=1.5, sources=["title"]),
        Topic(label="cuisinart", score=1.0, sources=["title"]),
    ]
    out = _merge_near_duplicates(sorted_topics)
    assert [t.label for t in out] == ["kitchen", "toaster", "cuisinart"]


def test_fuse_dedupes_near_duplicates_in_top_k():
    """End-to-end: fuse collapses 'X' / 'X Y' / 'X Y Z' chains before slicing."""
    heuristic = [
        TopicCandidate("python", 3.0, "title"),
        TopicCandidate("python software foundation", 1.5, "yake"),
        TopicCandidate("python enhancement proposals", 1.2, "yake"),
        TopicCandidate("programming", 1.0, "meta:keywords"),
        TopicCandidate("scripting", 0.8, "meta:keywords"),
    ]
    topics = fuse_topics(heuristic, [], top_k=3)
    labels = [t.label for t in topics]
    # subset phrases collapse into "python"; top-3 holds 3 distinct topics
    assert "python" in labels
    assert "programming" in labels
    assert "python software foundation" not in labels
    assert "python enhancement proposals" not in labels
