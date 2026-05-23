"""Tests for heuristic topic candidate extraction."""
from crawler.classifier.heuristics import candidates_from_meta_and_jsonld
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


def test_title_emits_phrase_and_tokens():
    meta = MetaTags(title="Cuisinart Compact 2-Slice Toaster")
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    labels = {c.label for c in cands}
    # full phrase preserved
    assert "cuisinart compact 2-slice toaster" in labels
    # content tokens (>= 4 chars, non-stopword) emitted
    assert "toaster" in labels
    assert "cuisinart" in labels
    assert "compact" in labels
    # sources tagged "title"
    assert all(c.source == "title" for c in cands if c.label == "toaster")


def test_title_split_on_separators():
    meta = MetaTags(title="Amazon.com: Cuisinart Toaster | Kitchen")
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    labels = {c.label for c in cands}
    # ":" and "|" split into separate chunks; tokens propagate
    assert "cuisinart toaster" in labels
    assert "toaster" in labels
    assert "kitchen" in labels


def test_title_filters_short_tokens_and_stopwords():
    meta = MetaTags(title="How to Make the Best Pasta")
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    labels = {c.label for c in cands}
    # stopwords and < 4 char tokens filtered
    assert "the" not in labels
    assert "how" not in labels
    assert "to" not in labels
    # content tokens kept
    assert "pasta" in labels
    assert "best" in labels


def test_h1_emits_candidates_with_h1_source():
    meta = MetaTags(h1=["Easy Chocolate Cake Recipe"])
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    h1_cands = [c for c in cands if c.source == "h1"]
    h1_labels = {c.label for c in h1_cands}
    assert "easy chocolate cake recipe" in h1_labels
    assert "chocolate" in h1_labels
    assert "recipe" in h1_labels


def test_description_emits_candidates_with_lower_weight_than_title():
    meta = MetaTags(
        title="Espresso Machine Guide",
        description="An overview of espresso machine brewing techniques.",
    )
    cands = candidates_from_meta_and_jsonld(meta, jsonld=[])
    espresso_title = next(c for c in cands if c.label == "espresso" and c.source == "title")
    espresso_desc = next(c for c in cands if c.label == "espresso" and c.source == "description")
    assert espresso_title.weight > espresso_desc.weight


def test_missing_title_and_h1_does_not_crash():
    cands = candidates_from_meta_and_jsonld(MetaTags(), jsonld=[])
    assert cands == []
