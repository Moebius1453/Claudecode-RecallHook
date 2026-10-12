"""候选卡分布体检（只读）：不排水，只看池子里是什么。

用法：
    python scripts\\card-distribution.py                  # 最近一批的分布
    python scripts\\card-distribution.py --topics         # 带上每张卡的主题
    python scripts\\card-distribution.py --since-id 751   # 指定本批从哪张卡起算

口径提醒（09-status「现库实况」）：`memctl status` 报的"待排水候选卡"含
`duplicate_of` 非空的卡；这里两个数分开报，说数的时候要说清是哪个。
"""

import argparse
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memlib import db

# 2026-10-08 这批之前的末张卡 id（09-status 里 751 张卡时的最大 id）。
PREVIOUS_BATCH_LAST_ID = 751


def load(db_path: Path):
    connection = db.connect(db_path)
    try:
        return connection.execute(
            "SELECT id, source_session, ts, topic, classification, status,"
            "       duplicate_of, card_vector IS NOT NULL, noise_flags"
            "  FROM candidate_cards ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


def report(label: str, batch, show_topics: bool) -> None:
    print(f"\n## {label} —— {len(batch)} 张")
    if not batch:
        return
    print(f"  status         {dict(Counter(r[5] for r in batch))}")
    duplicates = sum(1 for r in batch if r[6] is not None)
    print(f"  重复指向       {duplicates} 张｜独立 {len(batch) - duplicates} 张"
          f"（独立 = duplicate_of 为空，这才是要排的）")

    fresh = [r for r in batch if r[5] == "new" and r[6] is None]
    print(f"  池中独立待排   {len(fresh)} 张")
    print(f"  classification {dict(Counter(r[4] for r in fresh))}")

    embedded = sum(1 for r in fresh if r[7])
    note = "" if embedded == len(fresh) else "   ← 缺向量的卡去重判据会退化"
    print(f"  有向量         {embedded}/{len(fresh)}{note}")
    print(f"  带 noise_flags {sum(1 for r in fresh if r[8] not in ('[]', '', None))} 张")

    print("  按会话:")
    for session, count in Counter(r[1] for r in fresh).most_common():
        print(f"    {count:>4}  {session}")
    print("  按日期:")
    for day, count in sorted(Counter((r[2] or "?")[:10] for r in fresh).items()):
        print(f"    {day}  {count:>4}")

    if show_topics:
        print("  主题（id｜分类｜日期）:")
        for r in fresh:
            print(f"    #{r[0]}｜{r[4]}｜{(r[2] or '?')[:10]}｜{'无向量｜' if not r[7] else ''}{r[3][:70]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="候选卡分布体检（只读，不排水）")
    parser.add_argument("--since-id", type=int, default=PREVIOUS_BATCH_LAST_ID,
                        help=f"本批从哪张卡起算（默认 {PREVIOUS_BATCH_LAST_ID}）")
    parser.add_argument("--all", action="store_true", help="也报全库分布（不含主题）")
    parser.add_argument("--topics", action="store_true", help="打印每张独立待排卡的主题")
    args = parser.parse_args()

    config = db.load_config()
    rows = load(db.resolve_path(config["db"]["path"]))
    batch = [r for r in rows if r[0] > args.since_id]
    print(f"库中共 {len(rows)} 张卡｜本批 {len(batch)} 张（id > {args.since_id}）")
    report("本批", batch, args.topics)
    if args.all:
        report("全库", rows, False)


if __name__ == "__main__":
    main()
