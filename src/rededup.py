""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, dedup, store


def rebuild(connection, *, topic_threshold: float, full_threshold: float,
            dry_run: bool, log) -> dict:
    rows = store.all_card_rows(connection)
    stats = {"cards": len(rows), "before": 0, "after": 0, "skipped_discard": 0,
             "changed": 0}

    stats["before"] = sum(1 for r in rows if r["duplicate_of"] is not None)

    canonical: list[dedup.Card] = []
    decisions: dict[int, int] = {}          
    for row in rows:
        if row["classification"] == "discard":
            
            stats["skipped_discard"] += 1
            continue
        card = dedup.Card(id=row["id"], topic=row["topic"] or "",
                          summary=row["summary"] or "")
        dup_id, _score, _signal = dedup.find_duplicate(
            card, canonical, topic_threshold=topic_threshold,
            full_threshold=full_threshold)
        if dup_id is None:
            canonical.append(card)
        else:
            decisions[row["id"]] = dup_id

    stats["after"] = len(decisions)
    stats["changed"] = sum(
        1 for r in rows if decisions.get(r["id"]) != r["duplicate_of"])

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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def log(message: str) -> None:
        if not args.json:
            print(message, file=sys.stderr)

    config = db.load_config(args.config)
    screen_cfg = config.get("screen", {})
    topic_threshold = float(screen_cfg.get("dedup_topic_threshold", 0.30))
    full_threshold = float(screen_cfg.get("dedup_full_threshold", 0.10))

    db_path = args.db or db.resolve_path(config["db"]["path"])
    db.ensure_schema(db_path)
    connection = db.connect(db_path)
    try:
        stats = rebuild(connection, topic_threshold=topic_threshold,
                        full_threshold=full_threshold, dry_run=args.dry_run,
                        log=log)
    except Exception as error:  
        connection.rollback()
        parser.exit(1, f"rededup 失败：{type(error).__name__}: {error}\n")
    finally:
        connection.close()

    if args.json:
        import json
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"卡片 {stats['cards']}（跳过 discard {stats['skipped_discard']}）｜"
              f"标为重复 {stats['before']} → {stats['after']}｜"
              f"指针变化 {stats['changed']}"
              + ("（dry-run 未落盘）" if args.dry_run else ""))
        print(f"阈值：topic≥{topic_threshold} 且 full≥{full_threshold}")


if __name__ == "__main__":
    main()
