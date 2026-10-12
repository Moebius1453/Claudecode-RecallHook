""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, store
import screen


def main() -> None:
    parser = argparse.ArgumentParser(description="重算候选卡的支撑原文。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show", type=int, default=5, help="打印前 N 张的改前/改后")
    args = parser.parse_args()

    config = db.load_config(args.config)
    db_path = args.db or db.resolve_path(config["db"]["path"])
    connection = db.connect(db_path)

    changed = same = orphan = 0
    samples: list[tuple] = []
    for card in store.cards_all(connection):
        turns = store.card_turns(connection, card["id"])
        if not turns:
            orphan += 1
            continue
        fresh = screen.evidence_text(turns)
        if fresh == card["snippet"]:
            same += 1
            continue
        changed += 1
        if len(samples) < args.show:
            samples.append((card["id"], card["topic"], card["snippet"], fresh))
        if not args.dry_run:
            store.set_card_snippet(connection, card["id"], fresh)

    if not args.dry_run:
        connection.commit()

    print(f"共 {changed + same + orphan} 张卡：改 {changed}｜未变 {same}｜没有挂靠轮次 {orphan}")
    print("（dry-run，未写库）" if args.dry_run else "（已写库）")
    for card_id, topic, old, new in samples:
        print(f"\n--- #{card_id} {topic}")
        print(f"  旧：{(old or '(空)')[:150]}")
        print(f"  新：{(new or '(空)')[:150]}")


if __name__ == "__main__":
    main()
