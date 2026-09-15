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
    vector: object = None          

    @property
    def topic_fp(self) -> frozenset[str]:
        return frozenset(ngrams(self.topic or "", n=FP_N))

    @property
    def full_fp(self) -> frozenset[str]:
        return fingerprint(self.topic, self.summary)


def embed_text(topic: str, summary: str) -> str:
    ""
    return f"{topic or ''} {summary or ''}".strip()


def cosine(left, right) -> float:
    import numpy as np

    left = np.asarray(left, dtype=np.float32).ravel()
    right = np.asarray(right, dtype=np.float32).ravel()
    denominator = float(np.linalg.norm(left)) * float(np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(left @ right / denominator)


def find_duplicate_vector(new: Card, existing: list[Card], *,
                          threshold: float) -> tuple[int | None, float, str]:
    ""
    if new.vector is None:
        return None, 0.0, "vector"
    best_id, best_score = None, 0.0
    raw = 0.0
    for other in existing:
        if other.vector is None:
            continue
        score = cosine(new.vector, other.vector)
        if score > raw:
            raw = score
        if score >= threshold and score > best_score:
            best_id, best_score = other.id, score
    return best_id, raw, "vector"



def find_duplicate(new: Card, existing: list[Card], *,
                   topic_threshold: float,
                   full_threshold: float) -> tuple[int | None, float, str]:
    ""
    new_topic, new_full = new.topic_fp, new.full_fp
    if not new_topic and not new_full:
        return None, 0.0, ""

    best_id, best_score = None, 0.0
    raw_score, raw_signal = 0.0, ""
    for other in existing:
        topic_score = overlap(new_topic, other.topic_fp)
        full_score = jaccard(new_full, other.full_fp)
        
        if topic_score > raw_score:
            raw_score, raw_signal = topic_score, "topic"
        if full_score > raw_score:
            raw_score, raw_signal = full_score, "full"
        
        if topic_score < topic_threshold or full_score < full_threshold:
            continue
        combined = max(topic_score, full_score)
        if combined > best_score:
            best_id, best_score = other.id, combined
    return best_id, raw_score, raw_signal


def find_duplicate_by(method: str, new: Card, existing: list[Card], *,
                      vector_threshold: float = 0.68,
                      topic_threshold: float = 0.30,
                      full_threshold: float = 0.10
                      ) -> tuple[int | None, float, str]:
    ""
    if method == "vector" and new.vector is not None:
        return find_duplicate_vector(new, existing, threshold=vector_threshold)
    if method == "vector":
        found, score, _ = find_duplicate(new, existing, topic_threshold=topic_threshold,
                                         full_threshold=full_threshold)
        return found, score, "ngram(fallback)"
    return find_duplicate(new, existing, topic_threshold=topic_threshold,
                          full_threshold=full_threshold)
