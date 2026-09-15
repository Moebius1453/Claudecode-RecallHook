""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import coverage, db, embed, store


def main() -> None:
    parser = argparse.ArgumentParser(description="拿真实发言回放，量召回覆盖度。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--full", action="store_true",
                        help="加上 ngram/向量兜底口径（要嵌入服务）")
    parser.add_argument("--limit", type=int, default=0,
                        help="只用前 N 条发言当探针（默认全部）")
    parser.add_argument("--show", type=int, default=25,
                        help="明细最多列几行（0 = 全部）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def log(message: str) -> None:
        if not args.json:
            print(message, file=sys.stderr)

    config = db.load_config(args.config)
    db_path = args.db or db.resolve_path(config["db"]["path"])
    connection = db.connect(db_path)

    turns = coverage.user_turns(connection, limit=args.limit)
    if not turns:
        connection.close()
        parser.exit(1, "turns 表里没有用户发言 —— 先跑 capture.py\n")

    log(f"探针：{len(turns)} 条真实用户发言")
    cards = coverage.trigger_coverage(connection, turns)

    if args.full:
        embedder = embed.get_embedder(config, bulk=True)
        if not embedder.available:
            log(f"[degrade] 嵌入不可用（{embedder.reason}），模糊口径只算 ngram")
        log("跑完整检索（这一步慢）…")
        hits = coverage.fuzzy_coverage(connection, turns, config, embedder, log)
        for card in cards:
            card.fuzzy_hits = hits.get(card.file_id, 0)

    info = coverage.summarize(cards)
    workhorses = coverage.workhorse_triggers(connection, turns, limit=15)
    connection.close()

    if args.json:
        import json
        print(json.dumps({
            "probe_turns": len(turns), "summary": info,
            "files": [card.__dict__ for card in cards],
            "workhorse_triggers": [
                {"keyword": w, "hits": n, "file": f} for w, n, f in workhorses],
        }, ensure_ascii=False, indent=2))
        return

    print(f"探针 {len(turns)} 条真实发言 × {info['files']} 个在盘文件")
    print(f"触发词一条都没命中的：**{info['silent']} 个**"
          f"（覆盖到的 {info['covered']} 个）")
    print()

    shown = cards if not args.show else cards[:args.show]
    if args.full:
        print(f"  {'触发':>5} {'模糊':>5}  {'词数':>4}  {'档':<8} 文件")
    else:
        print(f"  {'触发':>5}  {'词数':>4}  {'档':<8} 文件")
    for card in shown:
        flag = "静默" if card.silent else "    "
        head = f"  {flag} {card.trigger_hits:>5}"
        if args.full:
            head += f" {card.fuzzy_hits:>5}"
        print(f"{head}  {len(card.triggers):>4}  {card.tier:<8} {card.name}")
    if args.show and len(cards) > args.show:
        print(f"  …（还有 {len(cards) - args.show} 个，用 --show 0 看全部）")

    if args.full:
        print()
        
        
        at_risk = [c for c in cards
                   if c.tier != "always" and c.trigger_hits == 0 and c.fuzzy_hits == 0]
        always_safe = [c for c in cards
                       if c.tier == "always" and c.trigger_hits == 0 and c.fuzzy_hits == 0]
        print(f"⚠️ 撤手写索引后会失联的（warm/resting 且两条路都召不回）：**{len(at_risk)} 个**")
        for card in at_risk:
            print(f"    {card.tier:<8} {card.name}")
        if always_safe:
            print(f"    （另有 {len(always_safe)} 个 always 档也召不回，但它们住在"
                  f"MEMORY.md 机器区块里，**不受影响**："
                  f"{', '.join(c.name for c in always_safe)}）")
        fallback_only = [c for c in cards
                         if c.tier != "always" and c.trigger_hits == 0 and c.fuzzy_hits > 0]
        if fallback_only:
            print(f"    （{len(fallback_only)} 个触发词为 0 但模糊能召回，不算失联）")

    print()
    print("真在干活的触发词：")
    for keyword, hits, name in workhorses:
        print(f"  {keyword:<14} {hits:>5} 条发言   → {name}")


if __name__ == "__main__":
    main()
