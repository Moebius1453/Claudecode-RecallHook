""

import argparse
import collections
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import cohesion, db, store, vector as vec


MIN_UTTERANCES = 3


TOP_K = 3


def build_tables(connection):
    ""
    trigger_files: dict[str, set[str]] = collections.defaultdict(set)
    for keyword, name in connection.execute(
        "SELECT t.keyword, f.name FROM triggers t JOIN files f ON f.id=t.file_id "
        "WHERE t.status='active'"
    ):
        trigger_files[keyword].add(name)

    rows = connection.execute(
        "SELECT name, description, desc_vector FROM files "
        "WHERE desc_vector IS NOT NULL AND missing_since IS NULL"
    ).fetchall()
    names = [row[0] for row in rows]
    descriptions = {row[0]: row[1] or "" for row in rows}
    matrix = np.stack([vec.unpack(row[2], 512) for row in rows])
    matrix /= (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return trigger_files, names, descriptions, matrix


def hubness(unit: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    ""
    return (unit @ matrix.T).mean(axis=0)


def centred(unit_rows: np.ndarray, matrix: np.ndarray, hub: np.ndarray) -> np.ndarray:
    return unit_rows @ matrix.T - hub


def audit_triggers(index, trigger_files, names, matrix, hub, min_n: int):
    ""
    lookup = {name: i for i, name in enumerate(names)}
    utterances = index.texts
    unit = index.unit

    reports = []
    for keyword, owners in sorted(trigger_files.items()):
        rows = [i for i, text in enumerate(utterances) if keyword in text]
        if len(rows) < min_n:
            continue
        score = centred(unit[rows], matrix, hub)
        hit, point_at = 0, collections.Counter()
        for vector in score:
            point_at[names[int(np.argmax(vector))]] += 1
            targets = [lookup[name] for name in owners if name in lookup]
            if targets:
                best = max(vector[t] for t in targets)
                if int((vector > best).sum()) < TOP_K:
                    hit += 1
        top_owner, top_count = point_at.most_common(1)[0]
        reports.append({
            "keyword": keyword,
            "owners": sorted(owners),
            "utterances": len(rows),
            "top3_rate": hit / len(rows),
            
            
            "points_at": top_owner,
            "points_at_share": top_count / len(rows),
            "misattributed": (top_owner not in owners) and (top_count / len(rows) >= 0.3),
        })
    reports.sort(key=lambda item: item["top3_rate"])
    return reports


def audit_files(index, trigger_files, names, descriptions, matrix, hub, min_n: int):
    ""
    lookup = {name: i for i, name in enumerate(names)}
    utterances = index.texts
    unit = index.unit

    by_file: dict[str, list[int]] = collections.defaultdict(list)
    for keyword, owners in trigger_files.items():
        rows = [i for i, text in enumerate(utterances) if keyword in text]
        for name in owners:
            by_file[name].extend(rows)

    reports = []
    for name, rows in by_file.items():
        unique = sorted(set(rows))
        if len(unique) < min_n or name not in lookup:
            continue
        score = centred(unit[unique], matrix, hub)
        own = score[:, lookup[name]]
        in_top3 = sum(1 for vector in score
                      if int((vector > vector[lookup[name]]).sum()) < TOP_K)
        reports.append({
            "file": name,
            "description": (descriptions.get(name) or "")[:60],
            "triggers": sum(1 for k, o in trigger_files.items() if name in o),
            "utterances": len(unique),
            "top3_rate": in_top3 / len(unique),
            "mean_corrected": float(own.mean()),
            "best_corrected": float(own.max()),
        })
    reports.sort(key=lambda item: item["top3_rate"])
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="触发词与文件的语义审计（探针，不 assert）。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--zone", type=float, default=0.2,
                        help="只列 top3 率低于这个值的触发词（0 = 全部）")
    parser.add_argument("--min-n", type=int, default=MIN_UTTERANCES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = db.load_config(args.config)
    connection = db.connect(args.db or db.resolve_path(config["db"]["path"]))

    index = cohesion.UtteranceIndex.load(connection, config, lambda _m: None)
    if index is None:
        parser.exit(1, "建不出向量索引 —— 先跑 capture.py 和 index_docs.py\n")

    trigger_files, names, descriptions, matrix = build_tables(connection)
    hub = hubness(index.unit, matrix)
    print(f"hub 度（文件对全部发言的平均余弦）中位 {np.median(hub):.3f}｜"
          f"最高 {hub.max():.3f}（{names[int(np.argmax(hub))]}）", file=sys.stderr)
    by_word = audit_triggers(index, trigger_files, names, matrix, hub, args.min_n)
    by_file = audit_files(index, trigger_files, names, descriptions, matrix, hub, args.min_n)

    if args.json:
        print(json.dumps({"triggers": by_word, "files": by_file},
                         ensure_ascii=False, indent=2))
        return

    suspect = [item for item in by_word if item["top3_rate"] < args.zone]
    print(f"在用触发词 {len(trigger_files)} 个｜发言 {len(index.texts)} 条"
          f"｜纳入统计 {len(by_word)} 个（发言 ≥{args.min_n}）")
    print(f"top3 率 < {args.zone} 的嫌疑词：**{len(suspect)} 个**\n")

    print("── 一、嫌疑触发词（top3 率升序）")
    print(f"  {'词':<12}{'top3率':>7}{'发言':>6}  {'归属是否可疑':<14}{'实际指向':<30}所属文件")
    for item in suspect:
        flag = "★ 归属可疑" if item["misattributed"] else ""
        print(f"  {item['keyword']:<12}{item['top3_rate']:>7.2f}{item['utterances']:>6}  "
              f"{flag:<14}{item['points_at'][:28]:<30}{'、'.join(item['owners'])[:26]}")

    print("\n── 二、描述与触发词最脱节的文件（平均余弦升序）")
    print(f"  {'文件':<30}{'触发词':>6}{'发言':>6}{'top3率':>8}{'校正均分':>9}")
    for item in by_file[:15]:
        print(f"  {item['file'][:28]:<30}{item['triggers']:>6}{item['utterances']:>6}"
              f"{item['top3_rate']:>8.2f}{item['mean_corrected']:>9.3f}")

    print("\n判读：**归属可疑** = 这些发言实际指向的是另一个文件 ⇒ 该改归属，不是归档。")
    print("      没标的 = 发言指向散乱 ⇒ 该词本身泛化，**考虑**归档。")
    print()
    print("⚠️ **别整批归档！** 实测 2026-09-17：按本表把 16 个嫌疑词一次处理掉，")
    print('   检索基线从 **15/18 掉到 13/18** —— `led` 和 `会话` 正在承担真实查询')
    print('   （"姿态检测的 led 灯" / "长会话是不是会变笨"）。')
    print("   **每处理一批，立刻跑 python tests/test_retrieval_baseline.py。**")
    print("   判错了用 `memctl unretire <词>` 撤回。")


if __name__ == "__main__":
    main()
