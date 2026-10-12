""

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np

from . import db, embed

CACHE_NAME = "utterance_vecs.npy"


UTTERANCE_CHARS = 400
BATCH = 16


@dataclass
class UtteranceIndex:
    texts: list[str]
    unit: np.ndarray          
    baseline: float
    pseudocount: float = 5.0
    delta_scale: float = 0.15

    def stats(self, word: str) -> tuple[int, float | None]:
        ""
        if not word:
            return 0, None
        rows = [i for i, text in enumerate(self.texts) if word in text]
        if len(rows) < 2:
            return len(rows), None
        block = self.unit[rows]
        similarity = block @ block.T
        count = len(rows)
        return count, float((similarity.sum() - np.trace(similarity)) / (count * (count - 1)))

    def specificity(self, word: str) -> float:
        count, cohesion = self.stats(word)
        if cohesion is None:
            return 1.0
        shrunk = (count * cohesion + self.pseudocount * self.baseline) / (
            count + self.pseudocount)
        delta = shrunk - self.baseline
        return max(0.3, min(1.0, 0.3 + 0.7 * (delta / self.delta_scale)))

    @classmethod
    def from_texts(cls, texts: list[str], vectors: np.ndarray,
                   pseudocount: float = 5.0, delta_scale: float = 0.15):
        valid = ~np.isnan(vectors).any(axis=1)
        clean = np.where(valid[:, None], vectors, 0.0).astype(np.float32)
        unit = clean / (np.linalg.norm(clean, axis=1, keepdims=True) + 1e-9)
        similarity = unit @ unit.T
        count = len(unit)
        baseline = float((similarity.sum() - np.trace(similarity)) / (count * (count - 1)))
        return cls(texts=texts, unit=unit, baseline=baseline,
                   pseudocount=pseudocount, delta_scale=delta_scale)

    @classmethod
    def load(cls, connection, config: dict, log=lambda _m: None,
             allow_embed: bool = True):
        ""
        rows = connection.execute(
            "SELECT text FROM turns WHERE role='user' AND text IS NOT NULL").fetchall()
        texts = [row[0][:UTTERANCE_CHARS] for row in rows if row[0] and row[0].strip()]
        if not texts:
            log("turns 里没有用户发言 —— 跳过具体度检查")
            return None

        settings = config.get("retrieval", {})
        pseudocount = float(settings.get("cohesion_pseudocount", 5))
        delta_scale = float(settings.get("cohesion_delta_scale", 0.15))

        cache = db.resolve_path(Path("data") / CACHE_NAME)
        if cache.exists():
            cached = np.load(cache)
            if cached.shape[0] == len(texts):
                return cls.from_texts(texts, cached, pseudocount, delta_scale)
            log(f"向量缓存条数不符（{cached.shape[0]} != {len(texts)}）")

        if not allow_embed:
            log("没有可用的向量缓存 —— 跳过具体度检查")
            return None

        embedder = embed.get_embedder(config, bulk=True)
        if not getattr(embedder, "available", True):
            log("嵌入服务不可用且无缓存 —— 跳过具体度检查")
            return None

        log(f"现算 {len(texts)} 条发言的向量…")
        dim = int(config.get("embed", {}).get("dim", 0))
        out: list[np.ndarray] = []
        for start in range(0, len(texts), BATCH):
            chunk = texts[start:start + BATCH]
            try:
                out.append(np.asarray(embedder.embed(chunk), dtype=np.float32))
            except Exception:  
                for text in chunk:
                    try:
                        out.append(np.asarray(embedder.embed([text]), dtype=np.float32))
                    except Exception:  
                        out.append(np.full((1, dim), np.nan, dtype=np.float32))
        vectors = np.vstack(out)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, vectors)
        return cls.from_texts(texts, vectors, pseudocount, delta_scale)


def main() -> None:  
    connection = db.connect(db.resolve_path(db.load_config()["db"]["path"]))
    index = UtteranceIndex.load(connection, db.load_config(), print)
    if index is None:
        sys.exit(1)
    for word in sys.argv[1:] or ["解析", "openbmc", "确认", "课表"]:
        count, cohesion = index.stats(word)
        print(f"  {word:<10} 发言 {count:>4}  具体度 {index.specificity(word):.2f}"
              + (f"  凝聚度 {cohesion:.3f}" if cohesion else ""))


if __name__ == "__main__":  
    main()
