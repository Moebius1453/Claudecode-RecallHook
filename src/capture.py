""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, store, transcript


DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def discover(projects_root: Path) -> list[Path]:
    ""
    if not projects_root.is_dir():
        return []
    return sorted(projects_root.rglob("*.jsonl"), key=lambda p: p.stat().st_size)


def run(connection, config: dict, *, projects_root: Path, limit: int = 0,
        dry_run: bool = False, log) -> dict:
    capture_cfg = config.get("capture", {})
    include_sidechain = bool(capture_cfg.get("include_sidechain", False))
    max_text_chars = int(capture_cfg.get("max_text_chars", transcript.DEFAULT_MAX_TEXT_CHARS))

    files = discover(projects_root)
    stats = {"files": 0, "files_skipped": 0, "lines": 0, "turns_new": 0,
             "turns_dup": 0, "turns_dropped": 0, "bytes": 0, "bad_json": 0}
    if limit:
        files = files[:limit]

    for path in files:
        offset = store.capture_offset(connection, str(path))
        size = path.stat().st_size
        if size < offset:
            
            
            log(f"  [warn] {path.name} 从 {offset} 缩到 {size} 字节，偏移作废，重扫")
            offset = 0
        elif size == offset:
            stats["files_skipped"] += 1
            continue

        turns, consumed, scan_stats = transcript.scan(
            path, offset, include_sidechain=include_sidechain,
            max_text_chars=max_text_chars)
        stats["files"] += 1
        stats["lines"] += scan_stats.lines
        stats["bad_json"] += scan_stats.bad_json

        if dry_run:
            stats["turns_new"] += scan_stats.turns
            continue

        fresh = 0
        for turn in turns:
            turn_id = store.insert_turn(
                connection, session_id=turn.session_id, source_path=str(path),
                role=turn.role, ts=turn.ts, uuid=turn.uuid, text=turn.text,
                text_hash=turn.text_hash)
            if turn_id is None:
                stats["turns_dup"] += 1
            else:
                fresh += 1
        
        store.set_capture_offset(connection, str(path), consumed)
        connection.commit()

        stats["turns_new"] += fresh
        stats["turns_dropped"] += scan_stats.turns - fresh
        stats["bytes"] += consumed - offset
        log(f"  {path.name[:8]} +{fresh} 轮"
            f"（去重 {scan_stats.turns - fresh}）｜{consumed - offset} 字节")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="jsonl 会话记录 → turns 暂存表。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--projects-root", type=Path, default=None,
                        help=f"默认 {DEFAULT_PROJECTS_ROOT}")
    parser.add_argument("--limit", type=int, default=0, help="最多处理几个文件")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    def log(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr)

    try:
        config = db.load_config(args.config)
        db_path = args.db or db.resolve_path(config["db"]["path"])
        db.ensure_schema(db_path)
        connection = db.connect(db_path)
    except (OSError, db.SchemaError, KeyError) as error:
        parser.exit(1, f"capture 失败：{error}\n")

    projects_root = args.projects_root
    if projects_root is None:
        configured = config.get("capture", {}).get("projects_root")
        projects_root = Path(configured) if configured else DEFAULT_PROJECTS_ROOT

    try:
        stats = run(connection, config, projects_root=projects_root,
                    limit=args.limit, dry_run=args.dry_run, log=log)
    except Exception as error:  
        connection.rollback()
        parser.exit(1, f"capture 失败：{type(error).__name__}: {error}\n")
    finally:
        connection.close()

    if args.json:
        import json
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"扫描文件 {stats['files']}（无变化跳过 {stats['files_skipped']}）｜"
              f"新轮次 {stats['turns_new']}｜哈希去重 {stats['turns_dup']}｜"
              f"读入 {stats['bytes'] / 1e6:.1f} MB｜坏 JSON 行 {stats['bad_json']}"
              + ("（dry-run 未落盘）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
