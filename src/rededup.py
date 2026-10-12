""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, dedup, embed, store
from memlib import vector as vec


def backfill_vectors(connection, embedder, *, dry_run: bool, log) -> dict:
    ""
    pending = store.cards_needing_vector(connection)
    fresh: dict = {}
    if not pending:
        return fresh
    if embedder is None or not getattr(embedder, "available", False):
        log(f"  [degrade] 嵌入不可用，{len(pending)} 张卡的向量补不上")
        return fresh
    batch = 32
    for start in range(0, len(pending), batch):
        chunk = pending[start:start + batch]
        try:
            matrix = embedder.embed([text for _, text in chunk])
        except Exception as error:  
            log(f"  [warn] 这批嵌入失败，跳过 {len(chunk)} 张：{error}")
            continue
        for (card_id, _), row in zip(chunk, matrix):
            fresh[card_id] = row
            if not dry_run:
                store.set_card_vector(connection, card_id, vec.pack(row))
        if not dry_run:
            connection.commit()
    log(f"  {'[dry] ' if dry_run else ''}补了 {len(fresh)} 张卡的向量")
    return fresh


def decide(rows: list[dict], *, method: str, embedder=None,
           extra_vectors: dict | None = None,
           vector_threshold: float = 0.68,
           topic_threshold: float = 0.30, full_threshold: float = 0.10
           ) -> tuple[dict[int, int], dict]:
    ""
    extra_vectors = extra_vectors or {}
    dim = getattr(embedder, "dim", 0) or 0
    stats = {"fallback": 0, "skipped_discard": 0,
             
             
             
             "vectors": sum(1 for r in rows
                            if r["card_vector"] or r["id"] in extra_vectors)}

    def vector_of(row):
        if row["id"] in extra_vectors:
            return extra_vectors[row["id"]]
        blob = row.get("card_vector")
        return vec.unpack(blob, dim) if (blob and dim) else None

    canonical: list[dedup.Card] = []
    decisions: dict[int, int] = {}
    for row in rows:
        if row["classification"] == "discard":
            stats["skipped_discard"] += 1
            continue
        card = dedup.Card(id=row["id"], topic=row["topic"] or "",
                          summary=row["summary"] or "", vector=vector_of(row))
        dup_id, _score, signal = dedup.find_duplicate_by(
            method, card, canonical,
            vector_threshold=vector_threshold,
            topic_threshold=topic_threshold, full_threshold=full_threshold)
        if signal == "ngram(fallback)":
            stats["fallback"] += 1
        if dup_id is None:
            canonical.append(card)
        else:
            decisions[row["id"]] = dup_id
    return decisions, stats


def load_rows(connection) -> list[dict]:
    return store.all_card_rows(connection)


def compare_methods(connection, *, embedder=None, extra_vectors: dict | None = None,
                    vector_threshold: float = 0.68,
                    topic_threshold: float = 0.30, full_threshold: float = 0.10
                    ) -> dict:
    ""
    rows = load_rows(connection)
    vector_decisions, _ = decide(rows, method="vector", embedder=embedder,
                                 extra_vectors=extra_vectors,
                                 vector_threshold=vector_threshold,
                                 topic_threshold=topic_threshold,
                                 full_threshold=full_threshold)
    ngram_decisions, _ = decide(rows, method="ngram", embedder=embedder,
                                extra_vectors=extra_vectors,
                                vector_threshold=vector_threshold,
                                topic_threshold=topic_threshold,
                                full_threshold=full_threshold)
    by_id = {row["id"]: row for row in rows}
    vector_only = sorted(set(vector_decisions) - set(ngram_decisions))
    ngram_only = sorted(set(ngram_decisions) - set(vector_decisions))
    both = sorted(set(vector_decisions) & set(ngram_decisions))
    return {"rows": rows, "by_id": by_id,
            "vector": vector_decisions, "ngram": ngram_decisions,
            "vector_only": vector_only, "ngram_only": ngram_only, "both": both}


def report_compare(result: dict, sample: int, active_method: str) -> None:
    ""
    by_id = result["by_id"]
    vector_only, ngram_only = result["vector_only"], result["ngram_only"]
    both = result["both"]
    print(f"向量判重 {len(result['vector'])}｜2-gram 判重 {len(result['ngram'])}")
    print(f"  两边都判重 {len(both)}")
    print(f"  只有向量判   {len(vector_only)}   ← 2-gram 漏掉的")
    print(f"  只有 2-gram 判 {len(ngram_only)}   ← 向量认为无关的")
    print(f"（config 里的 dedup_method = {active_method}）")
    print()

    def show(title: str, ids: list[int], hint: str) -> None:
        if not ids:
            return
        print(f"--- {title}（共 {len(ids)} 条，列最靠前的 {min(sample, len(ids))} 条）---")
        print(f"    {hint}")
        for card_id in ids[:sample]:
            row = by_id[card_id]
            parent = by_id.get(result["vector"].get(card_id)
                               or result["ngram"].get(card_id))
            print(f"  #{card_id} {row['topic'][:42]}")
            if parent:
                print(f"    ↳ #{parent['id']} {parent['topic'][:42]}")
        print()

    show("只有向量判为重复", vector_only, "这些是 2-gram 漏掉的 —— 看它们像不像同一件事")
    show("只有 2-gram 判为重复", ngram_only, "这些是向量认为无关的 —— 多数应该是 2-gram 的误判")


def rebuild(connection, *, method: str, embedder=None,
            extra_vectors: dict | None = None,
            vector_threshold: float = 0.68,
            topic_threshold: float = 0.30, full_threshold: float = 0.10,
            dry_run: bool, log) -> dict:
    rows = load_rows(connection)
    decisions, inner = decide(rows, method=method, embedder=embedder,
                              extra_vectors=extra_vectors,
                              vector_threshold=vector_threshold,
                              topic_threshold=topic_threshold,
                              full_threshold=full_threshold)
    stats = {"cards": len(rows), "skipped_discard": inner["skipped_discard"],
             "vectors": inner["vectors"], "fallback": inner["fallback"],
             "method": method,
             "before": sum(1 for r in rows if r["duplicate_of"] is not None),
             "after": len(decisions),
             "changed": sum(1 for r in rows
                            if decisions.get(r["id"]) != r["duplicate_of"])}

    if dry_run:
        return stats

    store.clear_card_duplicates(connection)
    for card_id, canonical_id in decisions.items():
        store.set_card_duplicate(connection, card_id, canonical_id)
    connection.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="重算所有候选卡的重复指向。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--method", choices=["vector", "ngram"], default=None,
                        help="判据。默认读 config.toml；**这是撤回来的开关**")
    parser.add_argument("--no-backfill", action="store_true",
                        help="不补缺失的向量（默认会补）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compare", action="store_true",
                        help="两种判据各跑一遍，看分歧（换不换判据的依据）")
    parser.add_argument("--sample", type=int, default=12,
                        help="--compare 时每种分歧列几个样本")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def log(message: str) -> None:
        if not args.json:
            print(message, file=sys.stderr)

    config = db.load_config(args.config)
    screen_cfg = config.get("screen", {})
    method = args.method or str(screen_cfg.get("dedup_method", "vector"))
    vector_threshold = float(screen_cfg.get("dedup_vector_threshold", 0.68))
    topic_threshold = float(screen_cfg.get("dedup_topic_threshold", 0.30))
    full_threshold = float(screen_cfg.get("dedup_full_threshold", 0.10))

    
    embedder = None
    if method == "vector":
        embedder = embed.get_embedder(config, bulk=True)
        if not embedder.available:
            log(f"[degrade] 嵌入不可用（{embedder.reason}）→ 降级到 2-gram")

    db_path = args.db or db.resolve_path(config["db"]["path"])
    db.ensure_schema(db_path)
    connection = db.connect(db_path)

    if args.compare:
        try:
            backfilled = {}
            if method == "vector" and not args.no_backfill:
                backfilled = backfill_vectors(connection, embedder,
                                              dry_run=True, log=log)
            result = compare_methods(connection, embedder=embedder,
                                     extra_vectors=backfilled,
                                     vector_threshold=vector_threshold,
                                     topic_threshold=topic_threshold,
                                     full_threshold=full_threshold)
        finally:
            connection.close()
        report_compare(result, args.sample, method)
        return

    try:
        backfilled: dict = {}
        if method == "vector" and not args.no_backfill:
            backfilled = backfill_vectors(connection, embedder,
                                          dry_run=args.dry_run, log=log)
        stats = rebuild(connection, method=method, embedder=embedder,
                        extra_vectors=backfilled,
                        vector_threshold=vector_threshold,
                        topic_threshold=topic_threshold,
                        full_threshold=full_threshold, dry_run=args.dry_run,
                        log=log)
        stats["backfilled"] = len(backfilled)
    except Exception as error:  
        connection.rollback()
        parser.exit(1, f"rededup 失败：{type(error).__name__}: {error}\n")
    finally:
        connection.close()

    if args.json:
        import json
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"判据 {method}｜卡片 {stats['cards']}"
              f"（跳过 discard {stats['skipped_discard']}）｜"
              f"标为重复 {stats['before']} → {stats['after']}｜"
              f"指针变化 {stats['changed']}"
              + ("（dry-run 未落盘）" if args.dry_run else ""))
        if method == "vector":
            print(f"  有向量的卡 {stats['vectors']}｜本次补 {stats['backfilled']}｜"
                  f"降级到 2-gram 的 {stats['fallback']}")
            print(f"  阈值：余弦 ≥ {vector_threshold}")
        else:
            print(f"  阈值：topic≥{topic_threshold} 且 full≥{full_threshold}")


if __name__ == "__main__":
    main()
