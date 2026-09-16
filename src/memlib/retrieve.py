""

from dataclasses import dataclass, field, replace
import sqlite3

from . import store, tfidf, vector as vec
from .text import ngrams, normalize, fts_query


@dataclass(frozen=True)
class Candidate:
    file_id: int
    path: str
    name: str
    description: str
    tier: str
    score: float
    source: str          
    keyword: str = ""    
    
    
    specificity: float = 1.0
    
    
    
    routes: dict = field(default_factory=dict)


@dataclass
class Result:
    query: str
    path_used: str       
    candidates: list[Candidate]
    skipped: list[str]   
    
    
    
    route_ranks: dict[int, dict[str, int]] = field(default_factory=dict)
    
    specific_floor: float = 0.6

    @property
    def empty(self) -> bool:
        return not self.candidates

    def confidence(self) -> str:
        ""
        if not self.candidates:
            return "none"
        top = self.candidates[0]
        if len(self.route_ranks.get(top.file_id, {})) >= 2:
            return "high"
        if top.source == "trigger":
            return "high" if top.specificity >= self.specific_floor else "medium"
        if top.source in ("vector", "tfidf"):
            return "medium"
        return "low"

    def ranks_of(self, file_id: int) -> str:
        ""
        import json
        return json.dumps(self.route_ranks.get(file_id, {}), ensure_ascii=False,
                          separators=(",", ":"), sort_keys=True)


class SkipStrategy(Exception):
    ""


def _candidate(item: dict, score: float, source: str, keyword: str = "",
               specificity: float = 1.0) -> Candidate:
    return Candidate(
        file_id=int(item["id"]),
        path=item["path"],
        name=item["name"],
        description=item["description"] or "",
        tier=item["tier"],
        score=score,
        source=source,
        keyword=keyword,
        specificity=specificity,
    )


def trigger_specificity(utterances, cohesion, context: dict) -> float:
    ""
    try:
        count = int(utterances) if utterances is not None else 0
    except (TypeError, ValueError):
        return 1.0
    if count < 2 or cohesion is None:
        return 1.0

    baseline = float(context.get("trigger_cohesion_baseline", 0.0))
    pseudo = float(context.get("trigger_cohesion_pseudocount", 5.0))
    delta_scale = float(context.get("trigger_specific_delta", 0.15))
    if delta_scale <= 0:
        return 1.0

    shrunk = (count * float(cohesion) + pseudo * baseline) / (count + pseudo)
    delta = shrunk - baseline
    return max(0.3, min(1.0, 0.3 + 0.7 * (delta / delta_scale)))




def strategy_trigger(connection: sqlite3.Connection, query: str, context: dict) -> list[Candidate]:
    ""
    rows = store.search_triggers(connection, query)
    candidates = []
    for item in rows:
        score = trigger_specificity(item.get("utterances"), item.get("cohesion"), context)
        candidates.append(
            _candidate(item, score, "trigger", item.get("keyword", ""), score))
    candidates.sort(key=lambda c: (-c.specificity, c.tier, c.path))
    return candidates


def strategy_ngram(connection: sqlite3.Connection, query: str, context: dict) -> list[Candidate]:
    ""
    size = int(context.get("ngram_n", 2))
    query_grams = set(ngrams(query, n=size))
    if not query_grams:
        return []
    threshold = float(context.get("ngram_threshold", 0.5))
    found = []
    for item in store.retrievable_docs(connection):
        text = f"{item['name']} {item['description'] or ''}"
        shared = query_grams & set(ngrams(text, n=size))
        if not shared:
            continue
        score = len(shared) / len(query_grams)
        if score >= threshold:
            found.append(_candidate(item, score, "ngram"))
    found.sort(key=lambda item: (-item.score, item.path))
    return found


def strategy_fts(connection: sqlite3.Connection, query: str, context: dict) -> list[Candidate]:
    ""
    grams = ngrams(query, n=context.get("fts_gram", 3),
                   limit=context.get("fts_max_grams", 40))
    if not grams:
        return []
    rows = store.search_fts(connection, fts_query(grams), limit=context.get("fts_limit", 20))
    return [_candidate(item, score, "fts") for item, score in rows]


def strategy_vector(connection: sqlite3.Connection, query: str, context: dict) -> list[Candidate]:
    embedder = context.get("embedder")
    if embedder is None or not embedder.available:
        raise SkipStrategy("向量不可用")
    rows = store.vector_rows(connection)
    if not rows:
        return []
    matrix = vec.stack([blob for _, blob in rows], embedder.dim)
    query_vector = embedder.embed([query])[0]
    threshold = float(context.get("vector_threshold", 0.50))
    found = []
    for index, score in vec.cosine_topk(query_vector, matrix, context.get("vector_topk", 10)):
        if score < threshold:
            continue
        found.append(_candidate(rows[index][0], score, "vector"))
    return found


def strategy_tfidf(connection: sqlite3.Connection, query: str, context: dict) -> list[Candidate]:
    ""
    stored_dim = store.get_meta(connection, "tfidf_dim")
    if stored_dim and int(stored_dim) != tfidf.DIM:
        raise SkipStrategy(
            f"TF-IDF 索引维度不符（库 {stored_dim} / 代码 {tfidf.DIM}），跑 index_docs.py 重建"
        )
    idf = tfidf.unpack_idf(store.get_meta(connection, "tfidf_idf"))
    rows = store.tfidf_rows(connection)
    if not rows:
        return []
    query_vector = vec.pack(tfidf.encode(query, idf))
    matrix = vec.stack([blob for _, blob in rows], tfidf.DIM)
    threshold = float(context.get("tfidf_threshold", 0.09))
    found = []
    for index, score in vec.cosine_topk(vec.unpack(query_vector, tfidf.DIM),
                                        matrix, context.get("vector_topk", 10)):
        if score < threshold:
            continue
        found.append(_candidate(rows[index][0], score, "tfidf"))
    return found


STRATEGIES = {
    "trigger": strategy_trigger,
    "ngram": strategy_ngram,
    "fts": strategy_fts,
    "vector": strategy_vector,
    "tfidf": strategy_tfidf,
}




def rrf_fuse(lists: dict[str, list[Candidate]], k: int,
             weights: dict[str, float]) -> tuple[list[Candidate], dict[int, dict[str, int]]]:
    ""
    scores: dict[int, float] = {}
    origin: dict[int, Candidate] = {}
    ranks: dict[int, dict[str, int]] = {}
    
    
    raw: dict[int, dict[str, float]] = {}
    for name, items in lists.items():
        weight = float(weights.get(name, 1.0))
        seen: set[int] = set()
        for candidate in items:
            key = candidate.file_id
            if key in seen:
                continue
            rank = len(seen)
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
            ranks.setdefault(key, {})[name] = rank
            raw.setdefault(key, {})[name] = float(candidate.score)
            
            origin.setdefault(key, candidate)

    fused = [replace(origin[key], score=score, routes=raw.get(key, {}))
             for key, score in scores.items()]
    
    fused.sort(key=lambda item: (store.TIER_RANK.get(item.tier, 9), -item.score, item.path))
    return fused, ranks


def retrieve(
    connection: sqlite3.Connection,
    query: str,
    *,
    order: list[str] | None = None,
    context: dict | None = None,
    limit: int = 0,
) -> Result:
    context = dict(context or {})
    order = order or context.get("order") or ["trigger", "ngram", "vector"]
    normalized = normalize(query)
    weights = context.get("weights") or {}
    skipped: list[str] = []
    lists: dict[str, list[Candidate]] = {}

    for name in order:
        strategy = STRATEGIES.get(name)
        if strategy is None:
            skipped.append(f"{name}：未注册的策略")
            continue
        try:
            found = strategy(connection, normalized, context)
        except SkipStrategy as reason:
            skipped.append(f"{name}：{reason}")
            continue
        if found:
            lists[name] = found

    if not lists:
        return Result(query=query, path_used="none", candidates=[], skipped=skipped)

    fused, ranks = rrf_fuse(lists, int(context.get("rrf_k", 60)), weights)
    if limit:
        fused = fused[:limit]
    return Result(query=query, path_used="+".join(lists), candidates=fused,
                  skipped=skipped, route_ranks=ranks,
                  specific_floor=float(context.get("trigger_specific_floor", 0.6)))


def build_context(config: dict, connection: sqlite3.Connection, embedder=None) -> dict:
    ""
    retrieval = config.get("retrieval", {})
    order = list(retrieval.get("order", ["trigger", "ngram", "vector"]))
    
    
    if embedder is None or not getattr(embedder, "available", False):
        if retrieval.get("tfidf_fallback", True):
            order = ["tfidf" if name == "vector" else name for name in order]

    context = {
        "order": order,
        "rrf_k": int(retrieval.get("rrf_k", 60)),
        "tfidf_threshold": float(retrieval.get("tfidf_threshold", 0.08)),
        "ngram_n": int(retrieval.get("ngram_n", 2)),
        "ngram_threshold": float(retrieval.get("ngram_threshold", 0.5)),
        "fts_gram": int(retrieval.get("fts_gram", 3)),
        "fts_max_grams": int(retrieval.get("fts_max_grams", 40)),
        "fts_limit": int(retrieval.get("fts_limit", 20)),
        "vector_topk": int(retrieval.get("vector_topk", 10)),
        "vector_threshold": float(
            store.get_meta(connection, "vector_threshold")
            or retrieval.get("vector_threshold", 0.50)
        ),
        "weights": {
            "trigger": float(retrieval.get("weight_trigger", 1.0)),
            "ngram": float(retrieval.get("weight_ngram", 0.8)),
            "vector": float(retrieval.get("weight_vector", 0.6)),
            "tfidf": float(retrieval.get("weight_vector", 0.6)),
        },
        "injection_max_lines": int(store.get_meta(connection, "injection_max_lines", "5")),
        
        
        "trigger_cohesion_baseline": float(
            store.get_meta(connection, "trigger_cohesion_baseline", "0.0") or 0.0
        ),
        "trigger_cohesion_pseudocount": float(
            store.get_meta(connection, "trigger_cohesion_pseudocount", "5") or 5
        ),
        "trigger_specific_delta": float(
            store.get_meta(connection, "trigger_specific_delta", "0.15") or 0.15
        ),
        "trigger_specific_floor": float(
            store.get_meta(connection, "trigger_specific_floor", "0.6") or 0.6
        ),
    }
    if embedder is not None:
        context["embedder"] = embedder
    return context
