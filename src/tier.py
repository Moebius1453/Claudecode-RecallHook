""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import always, db, frontmatter, regrade, store


def init_tiers(connection, *, apply: bool, log) -> dict:
    ""
    rows = store.rows_without_tier_events(connection)
    moved: list[tuple[str, str, str]] = []
    for row in rows:
        target = frontmatter.tier_for_kind(row["kind"])
        if target == row["tier"]:
            continue
        moved.append((row["name"], row["tier"], target))
        if apply:
            store.set_tier(connection, row["id"], target, reason="manual", actor="agent")
    for name, old, new in moved:
        log(f"  {name}：{old} → {new}")
    return {"considered": len(rows), "moved": moved}


def main() -> None:
    parser = argparse.ArgumentParser(description="档位初值回填 + always 同步进 MEMORY.md。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--init-tiers", action="store_true",
                        help="按 kind 回填从未被 tier 系统动过的行")
    parser.add_argument("--recompute", action="store_true",
                        help="升降级：刷新用量 → 晋升 → always 挤人 → 降级")
    parser.add_argument("--sync-always", action="store_true",
                        help="把 always 档写进 MEMORY.md")
    parser.add_argument("--dry-run", action="store_true", help="只算不写")
    parser.add_argument("--show-section", action="store_true",
                        help="打印将要写入 MEMORY.md 的那一段")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    
    explicit = args.init_tiers or args.recompute or args.sync_always
    run_init = args.init_tiers or not explicit
    run_recompute = args.recompute or not explicit
    run_sync = args.sync_always or not explicit

    result: dict = {}

    def log(message: str) -> None:
        if not args.json:
            print(message, file=sys.stderr)

    try:
        config = db.load_config(args.config)
        db_path = args.db or db.resolve_path(config["db"]["path"])
        db.ensure_schema(db_path)
        connection = db.connect(db_path)
    except (OSError, db.SchemaError, KeyError) as error:
        parser.exit(1, f"tier 失败：{error}\n")

    try:
        if run_init:
            result["init"] = init_tiers(connection, apply=not args.dry_run, log=log)
            if not args.dry_run:
                connection.commit()

        if run_recompute:
            result["regrade"] = regrade.regrade(connection, apply=not args.dry_run,
                                                log=log)
            if not args.dry_run:
                connection.commit()

        if run_sync:
            if args.show_section:
                print(always.render_section(connection))
            result["always"] = always.sync(connection, config, apply=not args.dry_run)
    except always.MarkerError as error:
        connection.rollback()
        parser.exit(1, f"tier 失败：{error}\n")
    except Exception as error:  
        connection.rollback()
        parser.exit(1, f"tier 失败：{type(error).__name__}: {error}\n")
    finally:
        connection.close()

    if args.json:
        import json
        from dataclasses import asdict
        
        
        print(json.dumps(result, ensure_ascii=False, indent=2, default=asdict))
        return

    if "init" in result:
        info = result["init"]
        print(f"初值回填：看过 {info['considered']} 行，改动 {len(info['moved'])} 行"
              + ("（dry-run 未落盘）" if args.dry_run else ""))
    if "regrade" in result:
        info = result["regrade"]
        stats, th = info["stats"], info["thresholds"]
        print(f"升降级：评审 {stats['reviewed']} 行｜晋升 {stats['promoted']}｜"
              f"挤人 {stats['evicted']}｜降级 {stats['demoted']}｜"
              f"跳过（pinned {stats['pinned']}／从未注入 {stats['never_injected']}）"
              + ("（dry-run 未落盘）" if args.dry_run else ""))
        if not stats["promoted"] and not stats["demoted"] and not stats["evicted"]:
            print(f"  （空转是正常的：判据来自 injection_log，"
                  f"hook 没上电时它一直是空的。阈值 "
                  f"{th['promotion_window_days']}天≥{th['promotion_min_injections']}次 / "
                  f"{th['warm_demotion_days']:.0f}天 / "
                  f"{th['resting_demotion_days']:.0f}天 / cap {th['always_cap']}）")
    if "always" in result:
        info = result["always"]
        if not info["ok"]:
            print(f"always 同步：跳过 —— {info['reason']}")
        else:
            print(f"always 同步：{info['action']}｜{info['path']}")
            if info["written"]:
                print(f"  备份 {info['backup']}")
            elif info["action"] != "unchanged":
                print("  （dry-run 未落盘）")


if __name__ == "__main__":
    main()
