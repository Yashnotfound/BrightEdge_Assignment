"""Tests for topic fusion."""
from crawler.classifier.fuse import Topic, fuse_topics
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
