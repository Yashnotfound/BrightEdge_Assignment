"""Merge heuristic + keyphrase candidates into ranked topics."""
from __future__ import annotations

from dataclasses import dataclass, field

from crawler.classifier.heuristics import TopicCandidate
from crawler.classifier.keyphrases import KeyphraseCandidate

# Two topic labels are treated as near-duplicates when token-set overlap on the
# shorter label reaches this threshold. Catches "python" ⊂ "python software
# foundation" and "neural networks" ⊂ "artificial neural networks".
#
# Note on single-token labels: normalizing by the *shorter* length means any
# single-token label (e.g., "python") that appears as a substring of a longer
# label (e.g., "python software foundation") has overlap 1.0 and absorbs it.
# This is the intended behavior — short labels are usually the canonical topic
# and longer phrases are variants worth collapsing — but it means a generic
# single token like "computer" can absorb a more specific phrase like
# "computer vision". That trade-off favors the common case (Wikipedia-style
# entity pages) over the long-tail case (arxiv-style multi-word topics).
_DEDUPE_OVERLAP_THRESHOLD = 0.7


@dataclass
class Topic:
    label: str
    score: float
    sources: list[str] = field(default_factory=list)


def _norm_label(label: str) -> str:
    return " ".join(label.strip().lower().split())


def _token_set_overlap(a: str, b: str) -> float:
    """Fraction of tokens shared, normalized by the shorter label's length."""
    ta = {t for t in a.split() if t}
    tb = {t for t in b.split() if t}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _merge_near_duplicates(topics: list[Topic]) -> list[Topic]:
    """Collapse near-duplicate topics into the higher-scoring one (in-order).

    Input must already be sorted by score descending. Sources are unioned,
    scores summed into the kept (higher-ranked) topic.
    """
    kept: list[Topic] = []
    for t in topics:
        merged = False
        for k in kept:
            if _token_set_overlap(t.label, k.label) >= _DEDUPE_OVERLAP_THRESHOLD:
                k.score += t.score
                for s in t.sources:
                    if s not in k.sources:
                        k.sources.append(s)
                merged = True
                break
        if not merged:
            kept.append(t)
    return kept


def fuse_topics(
    heuristic: list[TopicCandidate],
    keyphrase: list[KeyphraseCandidate],
    *,
    top_k: int = 10,
) -> list[Topic]:
    accum: dict[str, Topic] = {}

    for cand in heuristic:
        label = _norm_label(cand.label)
        if not label:
            continue
        topic = accum.setdefault(label, Topic(label=label, score=0.0))
        topic.score += cand.weight
        if cand.source not in topic.sources:
            topic.sources.append(cand.source)

    for cand in keyphrase:
        label = _norm_label(cand.label)
        if not label:
            continue
        topic = accum.setdefault(label, Topic(label=label, score=0.0))
        topic.score += cand.weight
        if "yake" not in topic.sources:
            topic.sources.append("yake")

    ranked = sorted(accum.values(), key=lambda t: t.score, reverse=True)
    # Collapse near-duplicates ("python" / "python software foundation") so each
    # top-K slot holds a distinct topic. Dedup runs BEFORE slicing so survivors
    # may inherit weight from displaced near-duplicates further down the list.
    deduped = _merge_near_duplicates(ranked)
    top = deduped[:top_k]

    if not top:
        return []

    max_score = top[0].score or 1.0
    for t in top:
        t.score = round(t.score / max_score, 4)
    return top
