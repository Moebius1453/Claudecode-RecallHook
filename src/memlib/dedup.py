""

from dataclasses import dataclass

from .text import ngrams


FP_N = 2


def fingerprint(topic: str, summary: str) -> frozenset[str]:
    ""
    return frozenset(ngrams(f"{topic or ''} {summary or ''}", n=FP_N))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def overlap(left: frozenset[str], right: frozenset[str]) -> float:
    ""
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


@dataclass(frozen=True)
class Card:
    ""
    id: int
    topic: str
    summary: str

    @property
    def topic_fp(self) -> frozenset[str]:
        return frozenset(ngrams(self.topic or "", n=FP_N))

    @property
    def full_fp(self) -> frozenset[str]:
        return fingerprint(self.topic, self.summary)


def find_duplicate(new: Card, existing: list[Card], *,
                   topic_threshold: float,
                   full_threshold: float) -> tuple[int | None, float, str]:
    ""
    new_topic, new_full = new.topic_fp, new.full_fp
    if not new_topic and not new_full:
        return None, 0.0, ""

    best_id, best_score, best_signal = None, 0.0, ""
    raw_score, raw_signal = 0.0, ""
    for other in existing:
        topic_score = overlap(new_topic, other.topic_fp)
        full_score = jaccard(new_full, other.full_fp)
        if topic_score > raw_score:
            raw_score, raw_signal = topic_score, "topic"
        if full_score > raw_score:
            raw_score, raw_signal = full_score, "full"
        if topic_score >= topic_threshold and topic_score > best_score:
            best_id, best_score, best_signal = other.id, topic_score, "topic"
        elif full_score >= full_threshold and full_score > best_score:
            best_id, best_score, best_signal = other.id, full_score, "full"
    return best_id, raw_score, raw_signal
