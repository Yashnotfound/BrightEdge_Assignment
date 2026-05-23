"""Tests for heuristic topic candidate extraction."""
from crawler.classifier.heuristics import TopicCandidate, candidates_from_meta_and_jsonld
from crawler.extractor.meta import MetaTags


def test_candidates_from_meta_keywords():
    meta = MetaTags(keywords=["toaster", "kitchen", "cuisinart"])
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    labels = {c.label for c in cands}
    assert "toaster" in labels
    assert "kitchen" in labels
    assert all(c.weight == 1.5 for c in cands if c.label in {"toaster", "kitchen"})


def test_candidates_from_og_type():
    meta = MetaTags(open_graph={"og:type": "product", "product:category": "Kitchen Toasters"})
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    labels = {c.label for c in cands}
    assert "product" in labels
    assert "kitchen toasters" in labels


def test_candidates_from_jsonld_category():
    jsonld = [{"@type": "Product", "category": "Kitchen > Small Appliances > Toasters"}]
    cands = candidates_from_meta_and_jsonld(MetaTags(), jsonld=jsonld)
    labels = {c.label for c in cands}
    assert "kitchen" in labels
    assert "small appliances" in labels
    assert "toasters" in labels


def test_candidates_from_jsonld_keywords_list():
    jsonld = [{"@type": "Article", "keywords": ["AI", "tech jobs"]}]
    cands = candidates_from_meta_and_jsonld(MetaTags(), jsonld=jsonld)
    labels = {c.label for c in cands}
    assert "ai" in labels
    assert "tech jobs" in labels


def test_candidates_dedupe_across_sources():
    meta = MetaTags(keywords=["toaster"], open_graph={"product:category": "Toaster"})
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    # both signals contribute, fuse later will merge
    assert sum(1 for c in cands if c.label == "toaster") == 2
