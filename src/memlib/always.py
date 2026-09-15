""

from pathlib import Path
import datetime as dt
import os
import shutil
import sqlite3

from . import corpus, db, render, store


MEMORY_NAME = "MEMORY.md"
MARKER_BEGIN = "<!-- always:begin 由 tier.py 生成，勿手改；改档位用 memctl tier-set -->"
MARKER_END = "<!-- always:end -->"



INSERT_ANCHOR = "# 记忆索引"

HEADING = "## 常驻记忆（always 档）"
BACKUP_DIR = db.MAIN_DIR / "data" / "memory_backups"


KIND_RANK = {"rule": 0, "profile": 1, "project": 2, "know-how": 3}


class MarkerError(RuntimeError):
    ""


def memory_root(config: dict) -> Path | None:
    ""
    for root in corpus.enabled_roots(config):
        if root.get("label") == "memory":
            return Path(root["path"])
    return None


def memory_md_path(config: dict) -> Path | None:
    root = memory_root(config)
    return None if root is None else root / MEMORY_NAME


def select(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    ""
    cap = int(store.get_meta(connection, "always_cap", "15") or 15)
    rows = [row for row in store.always_rows(connection)
            if (row["description"] or "").strip()]
    rows.sort(key=lambda row: (KIND_RANK.get(row["kind"], 9), row["path"]))
    if len(rows) <= cap:
        return rows, []
    evicted = sorted(
        rows,
        key=lambda row: (int(row["inject_count"] or 0),
                         -KIND_RANK.get(row["kind"], 9), row["path"]),
    )[: len(rows) - cap]
    evicted_ids = {row["id"] for row in evicted}
    return [row for row in rows if row["id"] not in evicted_ids], evicted


def entry_line(row: dict, limit: int) -> str:
    ""
    label = f"[{row['name']}]({Path(row['path']).name})"
    description = (row["description"] or "").strip()
    if not description:
        return render.clip(label, limit)
    room = limit - len(label) - 3
    if room < 6:
        return render.clip(label, limit)
    return f"{label} — {render.clip(description, room)}"


def render_section(connection: sqlite3.Connection) -> str:
    ""
    cap = int(store.get_meta(connection, "always_cap", "15") or 15)
    limit = int(store.get_meta(connection, "always_line_chars", "120") or 120)
    rows, evicted = select(connection)

    lines = [MARKER_BEGIN, "", HEADING, ""]
    if not rows:
        lines.append("_（暂无 always 档文件）_")
    else:
        lines.append(f"_{len(rows)}/{cap} 条，由 tier.py 维护；区外内容不受影响。_")
        lines.append("")
        for row in rows:
            lines.append(f"- {entry_line(row, limit)}")
    if evicted:
        names = "、".join(row["name"] for row in evicted)
        lines += ["", f"_超编未列出（always_cap={cap}，按注入次数挤人）：{names}_"]
    lines += ["", MARKER_END]
    return "\n".join(lines)


def splice(text: str, section: str) -> tuple[str, str]:
    ""
    begin = text.find(MARKER_BEGIN)
    end = text.find(MARKER_END)
    if begin >= 0 and end > begin:
        end += len(MARKER_END)
        if text[begin:end] == section:
            return text, "unchanged"
        return text[:begin] + section + text[end:], "replaced"
    if begin >= 0 or end >= 0:
        raise MarkerError(
            "MEMORY.md 里的 always 标记不成对（只找到一个）。"
            "拒绝改写 —— 请人工确认标记位置后再跑。"
        )
    anchor = text.find(INSERT_ANCHOR)
    if anchor < 0:
        return f"{text.rstrip()}\n\n{section}\n", "appended"
    return text[:anchor] + section + "\n\n" + text[anchor:], "inserted"


def backup(path: Path, stamp: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, target)
    return target


def write_atomic(path: Path, text: str) -> None:
    ""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def sync(connection: sqlite3.Connection, config: dict, *,
         apply: bool = True) -> dict:
    ""
    path = memory_md_path(config)
    if path is None:
        return {"ok": False, "reason": "config.toml 里没有启用的 memory 语料根"}
    if not path.exists():
        return {"ok": False, "reason": f"MEMORY.md 不存在：{path}", "path": str(path)}

    section = render_section(connection)
    original = path.read_text(encoding="utf-8")
    updated, action = splice(original, section)

    result = {
        "ok": True,
        "path": str(path),
        "action": action,
        "section_chars": len(section),
        "delta_chars": len(updated) - len(original),
        "written": False,
        "backup": None,
    }
    if action == "unchanged" or not apply:
        return result

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    result["backup"] = str(backup(path, stamp))
    write_atomic(path, updated)
    result["written"] = True
    return result


__all__ = [
    "MarkerError", "MARKER_BEGIN", "MARKER_END", "MEMORY_NAME",
    "memory_md_path", "memory_root", "render_section", "select", "splice", "sync",
]
