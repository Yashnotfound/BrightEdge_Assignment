"""Merge heuristic + keyphrase candidates into ranked topics."""
from __future__ import annotations

from dataclasses import dataclass, field

from crawler.classifier.heuristics import TopicCandidate
from crawler.classifier.keyphrases import KeyphraseCandidate


@dataclass
class Topic:
    label: str
    score: float
    sources: list[str] = field(default_factory=list)


def _norm_label(label: str) -> str:
    return " ".join(label.strip().lower().split())


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
    top = ranked[:top_k]

    if not top:
        return []

    max_score = top[0].score or 1.0
    for t in top:
        t.score = round(t.score / max_score, 4)
    return top
