""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import cohesion, db, store


def main() -> None:
    parser = argparse.ArgumentParser(description="量触发词的查询侧具体度（软信号）。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--top", type=int, default=20, help="列最泛化的前 N 个（0 = 不列）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="只算不写库")
    args = parser.parse_args()

    def log(message: str) -> None:
        if not args.json:
            print(message, file=sys.stderr)

    config = db.load_config(args.config)
    db_path = args.db or db.resolve_path(config["db"]["path"])
    db.ensure_schema(db_path)
    connection = db.connect(db_path)

    index = cohesion.UtteranceIndex.load(connection, config, log)
    if index is None:
        parser.exit(1, "建不出向量索引 —— 检查 turns 表或嵌入服务\n")
    log(f"发言样本：{len(index.texts)} 条")
    log(f"语料 baseline（全样本平均两两余弦）：{index.baseline:.4f}")

    keywords = [row[0] for row in connection.execute(
        "SELECT DISTINCT keyword FROM triggers WHERE status='active'")]
    log(f"在用触发词：{len(keywords)} 个")

    stats = []
    for keyword in keywords:
        count, value = index.stats(keyword)
        stats.append({"keyword": keyword, "utterances": count, "cohesion": value,
                      "specificity": index.specificity(keyword)})

    if not args.no_write:
        connection.executemany(
            "UPDATE triggers SET utterances=?, cohesion=? WHERE keyword=?",
            [(s["utterances"], s["cohesion"], s["keyword"]) for s in stats],
        )
        store.set_meta(connection, "trigger_cohesion_baseline", f"{index.baseline:.6f}")
        connection.commit()
        log("已写库：triggers.utterances / triggers.cohesion + meta.trigger_cohesion_baseline")

    if args.json:
        print(json.dumps(
            {"utterances_total": len(index.texts), "baseline": index.baseline,
             "triggers": stats}, ensure_ascii=False, indent=2))
        return

    known = sorted((s for s in stats if s["cohesion"] is not None),
                   key=lambda s: s["cohesion"])
    log(f"算得出凝聚度的：{len(known)} 个（其余命中的发言 < 2 条 —— 判不出来就不罚）")
    if args.top:
        for title, rows in (("最泛化的", known[:args.top]),
                            ("最具体的", known[-args.top:][::-1])):
            print(f"\n{title} {len(rows)} 个：")
            print(f"  {'词':<12}{'发言':>6}{'凝聚度':>9}{'vs baseline':>13}{'具体度':>8}")
            for item in rows:
                print(f"  {item['keyword']:<12}{item['utterances']:>6}"
                      f"{item['cohesion']:>9.3f}"
                      f"{item['cohesion'] - index.baseline:>+13.3f}"
                      f"{item['specificity']:>8.2f}")


if __name__ == "__main__":
    main()
