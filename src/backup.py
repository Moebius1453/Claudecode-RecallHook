""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db


WORKSPACE = db.MAIN_DIR.parent          
BACKUP_ROOT = WORKSPACE / "备份"
CLAUDE_HOME = Path.home() / ".claude"


SKIP_DIRS = {"__pycache__", "data", "models", "logs", ".git", "tmp"}


def _stamp() -> str:
    ""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def git_head(repo: Path) -> str:
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                              capture_output=True, text=True)
        return done.stdout.strip() or "(未知)"
    except OSError:
        return "(不是 git 仓库)"


def copy_db(source: Path, target: Path) -> str:
    ""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return f"{target.stat().st_size / 1e6:.1f} MB"


def copy_tree(source: Path, target: Path, *, skip: set[str] = frozenset()) -> int:
    ""
    count = 0
    for item in source.rglob("*"):
        if any(part in skip for part in item.relative_to(source).parts):
            continue
        if not item.is_file():
            continue
        destination = target / item.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        count += 1
    return count


def run(label: str | None) -> Path:
    config = db.load_config()
    db_path = db.resolve_path(config["db"]["path"])
    memory_dir = None
    for root in config.get("corpus", []):
        if root.get("label") == "memory":
            memory_dir = Path(root["path"])
            break

    name = "-".join(part for part in (_stamp(), label) if part)
    target = BACKUP_ROOT / name
    if target.exists():
        raise SystemExit(f"备份目录已存在：{target}")
    target.mkdir(parents=True)

    notes: list[tuple[str, str]] = []

    if db_path.exists():
        size = copy_db(db_path, target / "mem.db")
        cards = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM candidate_cards").fetchone()[0]
        notes.append(("mem.db", f"状态真相（{size}，候选卡 {cards} 张）"))
    else:
        notes.append(("mem.db", "**源文件不存在**"))

    if memory_dir and memory_dir.is_dir():
        count = copy_tree(memory_dir, target / "memory")
        size = sum(p.stat().st_size for p in (target / "memory").rglob("*")
                   if p.is_file())
        notes.append(("memory/", f"**内容真相**：{count} 个文件 / {size / 1024:.0f} KB"))
    else:
        notes.append(("memory/", "**memory 目录没找到**"))

    for name_json in ("settings.json", "settings.local.json"):
        source = CLAUDE_HOME / name_json
        if source.exists():
            shutil.copy2(source, target / name_json)
            notes.append((name_json, f"客户端配置（{source.stat().st_size} 字节）"))

    count = copy_tree(db.MAIN_DIR, target / "main", skip=SKIP_DIRS)
    notes.append(("main/", f"代码快照 {count} 个文件（models/data/logs/.git 已跳过）"))

    head = git_head(db.MAIN_DIR)
    manifest = [
        f"# 快照 {name}",
        "",
        f"- 时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}（UTC）",
        f"- 代码 HEAD：`{head}`（完整历史仍在 `main/.git`，这里只是文件快照）",
        f"- 工作区：`{WORKSPACE}`",
        "",
        "## 内容",
        "",
        "| 项 | 说明 |",
        "|---|---|",
    ]
    manifest += [f"| `{path}` | {note} |" for path, note in notes]
    manifest += [
        "",
        "## 怎么还原",
        "",
        "```powershell",
        f"Copy-Item '{target}\\mem.db' '{db_path}' -Force",
    ]
    if memory_dir:
        manifest.append(f"# memory 目录：整目录覆盖（先确认！这是人格层）")
        manifest.append(f"# Copy-Item '{target}\\memory\\*' '{memory_dir}' -Force -Recurse")
    manifest += [
        "```",
        "",
        "**只还原需要的那一项** —— 全量覆盖 memory 目录是破坏性操作，想清楚再动。",
    ]
    (target / "MANIFEST.md").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="变更前快照（DB + memory + 配置 + 代码）")
    parser.add_argument("--label", default=None, help="给快照起个名，如 上电前")
    args = parser.parse_args()
    try:
        target = run(args.label)
    except Exception as error:  
        raise SystemExit(f"备份失败：{type(error).__name__}: {error}")
    print(f"快照已存：{target}")
    for line in (target / "MANIFEST.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| `"):
            print("  " + line)


if __name__ == "__main__":
    main()
