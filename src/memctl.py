""

import argparse
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, embed, frontmatter, retrieve, store


def _open(args):
    config = db.load_config(args.config)
    db_path = args.db or db.resolve_path(config["db"]["path"])
    db.ensure_schema(db_path)
    return config, db.connect(db_path)


def _resolve_file(connection, name: str) -> dict | None:
    return store.find_file_by_name(connection, name)


def cmd_status(connection, args) -> int:
    info = store.status(connection)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print(f"schema v{info['schema_version']}｜文件 {info['files_total']}"
          f"（缺失 {info['files_missing']}）")
    print(f"档位 {info['by_tier']}")
    print(f"类型 {info['by_kind']}")
    print(f"无 description {info['files_no_description']}｜"
          f"无向量 {info['files_no_vector']}｜"
          f"无触发词 {info['files_no_trigger']}｜"
          f"触发词 {info['triggers_total']} {info['triggers_by_source']}")
    print(f"待排水候选卡 {info['cards_new']}｜向量模型 {info['embed_model'] or '（未嵌入过）'}")
    return 0


def cmd_query(connection, args) -> int:
    config = args._config
    embedder = None if args.no_embed else embed.get_embedder(config)
    context = retrieve.build_context(config, connection, embedder)
    if args.order:
        context["order"] = args.order.split(",")
    result = retrieve.retrieve(
        connection, " ".join(args.text), context=context, limit=args.limit
    )
    for reason in result.skipped:
        print(f"[skip] {reason}", file=sys.stderr)

    if args.json:
        print(json.dumps(
            {
                "query": result.query,
                "path_used": result.path_used,
                "candidates": [candidate.__dict__ for candidate in result.candidates],
            },
            ensure_ascii=False, indent=2,
        ))
        return 0

    if result.empty:
        print(f"无命中（走了 {result.path_used}）")
        return 0
    print(f"命中 {len(result.candidates)} 条，走过 {result.path_used}：")
    for candidate in result.candidates:
        keyword = f" «{candidate.keyword}»" if candidate.keyword else ""
        print(f"  [{candidate.tier}/{candidate.source} {candidate.score:.3f}]{keyword} "
              f"{candidate.name} — {candidate.description}")
    return 0


def cmd_files(connection, args) -> int:
    rows = store.list_files(
        connection, tier=args.tier, kind=args.kind, status=args.status,
        missing_only=args.missing, limit=args.limit,
    )
    if args.json:
        print(json.dumps(
            [{key: value for key, value in row.items() if key != "desc_vector"} for row in rows],
            ensure_ascii=False, indent=2,
        ))
        return 0
    for row in rows:
        flag = " [缺失]" if row["missing_since"] else ""
        print(f"  {row['tier']:<8} {row['kind']:<9} {row['name']}{flag}")
    print(f"共 {len(rows)} 行")
    return 0


def cmd_triggers(connection, args) -> int:
    if args.name:
        found = _resolve_file(connection, args.name)
        if found is None:
            print(f"没有 name={args.name} 的文件", file=sys.stderr)
            return 1
        rows = store.list_trigger_rows(connection, found["id"])
        if args.json:
            print(json.dumps(
                [{"keyword": w, "source": s, "status": st} for w, s, st in rows],
                ensure_ascii=False))
        elif not rows:
            print("（无触发词）")
        else:
            print("  ".join(
                f"{word}" + (f"[{source[:3]}]" if source != "frontmatter" else "")
                + ("×" if status == "rejected" else "")
                for word, source, status in rows
            ))
        return 0

    if args.rejected:
        rows = store.rejected_triggers(connection, limit=args.limit or 40)
        if args.json:
            print(json.dumps(
                [{"file": f, "keyword": w, "reason": r} for f, w, r in rows],
                ensure_ascii=False, indent=2))
        else:
            print(f"被拒候选（{len(rows)} 条，最多显示 {args.limit or 40}）：")
            for name, word, reason in rows:
                print(f"  {word:<10} {reason:<28} {name}")
        return 0

    missing = store.files_without_triggers(connection)
    counts = store.trigger_counts(connection)
    if args.json:
        print(json.dumps({"missing": missing, "counts": counts}, ensure_ascii=False, indent=2))
        return 0
    print(f"触发词 {counts}")
    print(f"无触发词的文件 {len(missing)} 个：")
    for name in missing:
        print(f"  {name}")
    return 0


def cmd_trigger_edit(connection, args, *, add: bool) -> int:
    found = _resolve_file(connection, args.name)
    if found is None:
        print(f"没有 name={args.name} 的文件", file=sys.stderr)
        return 1
    added: list[str] = []
    rejected: list[str] = []
    if add:
        added, rejected = store.add_triggers(connection, found["id"], args.keywords)
        message = f"新增 {added}"
    else:
        message = f"删除 {store.remove_triggers(connection, found['id'], args.keywords)}"
    if rejected:
        message += f"｜被拒 {rejected}"
    connection.commit()
    print(f"{args.name}: {message}")
    return 0


def cmd_tier_set(connection, args) -> int:
    found = _resolve_file(connection, args.name)
    if found is None:
        print(f"没有 name={args.name} 的文件", file=sys.stderr)
        return 1
    old = store.set_tier(connection, found["id"], args.tier, args.reason, args.actor)
    connection.commit()
    if old is None:
        print(f"{args.name}: 已在 {args.tier}，未变更")
    else:
        print(f"{args.name}: {old} → {args.tier}（{args.reason}/{args.actor}）")
    return 0


def cmd_meta(connection, args) -> int:
    ""
    if args.key and args.value is not None:
        store.set_meta(connection, args.key, args.value)
        connection.commit()
        print(f"{args.key} = {args.value}")
        return 0
    if args.key:
        print(store.get_meta(connection, args.key) or "")
        return 0
    rows = connection.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
    for key, value in rows:
        print(f"  {key:<28} {value}")
    return 0


def cmd_frontmatter_export(connection, args) -> int:
    ""
    rows = store.list_files(connection, limit=args.limit)
    blocks = []
    for row in rows:
        meta = frontmatter.Meta(
            name=row["name"],
            description=row["description"] or "",
            kind=row["kind"],
            status=row["status"],
            triggers=[word for word, _, status in
                      store.list_trigger_rows(connection, row["id"])
                      if status == "active"],
        )
        blocks.append(f"# {row['path']}\n" + frontmatter.render_frontmatter(meta))
    print("\n\n".join(blocks))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="记忆系统控制台。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("status", "总览"), ("query", "检索")):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true")
        if name == "query":
            sub.add_argument("text", nargs="+")
            sub.add_argument("--limit", type=int, default=5)
            sub.add_argument("--order", default="", help="覆盖策略顺序，如 trigger,fts")
            sub.add_argument("--no-embed", action="store_true")

    files = subparsers.add_parser("files", help="列出文件")
    files.add_argument("--tier", choices=list(store.TIER_RANK))
    files.add_argument("--kind", choices=["rule", "profile", "project", "know-how"])
    files.add_argument("--status")
    files.add_argument("--missing", action="store_true")
    files.add_argument("--limit", type=int, default=0)
    files.add_argument("--json", action="store_true")

    triggers = subparsers.add_parser("triggers", help="触发词总览 / 单文件明细 / 被拒候选")
    triggers.add_argument("name", nargs="?")
    triggers.add_argument("--rejected", action="store_true", help="列出被闸门拒掉的候选")
    triggers.add_argument("--limit", type=int, default=0)
    triggers.add_argument("--json", action="store_true")

    trigger_add = subparsers.add_parser("trigger-add", help="给文件加触发词（写 DB）")
    trigger_add.add_argument("name")
    trigger_add.add_argument("keywords", nargs="+")

    trigger_rm = subparsers.add_parser("trigger-rm", help="删除触发词")
    trigger_rm.add_argument("name")
    trigger_rm.add_argument("keywords", nargs="+")

    tier = subparsers.add_parser("tier-set", help="改档位并记 tier_events")
    tier.add_argument("name")
    tier.add_argument("tier", choices=list(store.TIER_RANK))
    tier.add_argument("--reason", default="manual",
                      choices=["time", "frequency", "pin", "unpin", "manual", "cap_eviction"])
    tier.add_argument("--actor", default="user", choices=["auto", "user", "agent"])

    export = subparsers.add_parser("frontmatter-export", help="打印 frontmatter 片段（不写 md）")
    export.add_argument("--limit", type=int, default=0)

    meta = subparsers.add_parser("meta", help="查看/修改阈值（调参入口）")
    meta.add_argument("key", nargs="?")
    meta.add_argument("value", nargs="?")

    args = parser.parse_args()
    try:
        config, connection = _open(args)
    except (OSError, db.SchemaError, KeyError) as error:
        parser.exit(1, f"memctl 失败：{error}\n")
    args._config = config
    try:
        if args.command == "status":
            code = cmd_status(connection, args)
        elif args.command == "query":
            code = cmd_query(connection, args)
        elif args.command == "files":
            code = cmd_files(connection, args)
        elif args.command == "triggers":
            code = cmd_triggers(connection, args)
        elif args.command == "trigger-add":
            code = cmd_trigger_edit(connection, args, add=True)
        elif args.command == "trigger-rm":
            code = cmd_trigger_edit(connection, args, add=False)
        elif args.command == "tier-set":
            code = cmd_tier_set(connection, args)
        elif args.command == "meta":
            code = cmd_meta(connection, args)
        else:
            code = cmd_frontmatter_export(connection, args)
    finally:
        connection.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
