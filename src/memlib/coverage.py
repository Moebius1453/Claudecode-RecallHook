""

from dataclasses import dataclass, field
import sqlite3

from . import store
from .text import normalize


@dataclass
class FileCoverage:
    file_id: int
    name: str
    kind: str
    tier: str
    description: str
    triggers: list[str] = field(default_factory=list)
    trigger_hits: int = 0                  
    inject_count: int = 0                  
    fuzzy_hits: int = 0                    
    example: str = ""                      

    @property
    def silent(self) -> bool:
        ""
        return self.trigger_hits == 0

    def line(self) -> str:
        flag = "静默" if self.silent else "    "
        return (f"  {flag} {self.trigger_hits:>5}  {len(self.triggers):>4}  "
                f"{self.tier:<8} {self.name}")


def user_turns(connection: sqlite3.Connection, limit: int = 0) -> list[str]:
    ""
    sql = "SELECT text FROM turns WHERE role='user' ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [normalize(row[0]) for row in connection.execute(sql)]


def trigger_coverage(connection: sqlite3.Connection,
                     turns: list[str]) -> list[FileCoverage]:
    ""
    rows = store.list_files(connection, limit=0)
    by_id: dict[int, FileCoverage] = {}
    for row in rows:
        if row["missing_since"]:
            continue
        by_id[row["id"]] = FileCoverage(
            file_id=row["id"], name=row["name"], kind=row["kind"],
            tier=row["tier"], description=row["description"] or "",
            inject_count=int(row["inject_count"] or 0))

    
    for file_id, keyword in connection.execute(
            "SELECT file_id, keyword FROM triggers WHERE status='active'"):
        card = by_id.get(file_id)
        if card is not None:
            card.triggers.append(keyword)

    for turn in turns:
        for card in by_id.values():
            for keyword in card.triggers:
                if keyword in turn:
                    card.trigger_hits += 1
                    if not card.example:
                        card.example = turn[:60]
                    break          
    return sorted(by_id.values(), key=lambda c: (c.trigger_hits, c.name))


def fuzzy_coverage(connection: sqlite3.Connection, turns: list[str], config: dict,
                   embedder, log) -> dict[int, int]:
    ""
    from . import retrieve

    context = retrieve.build_context(config, connection, embedder)
    hits: dict[int, int] = {}
    for index, turn in enumerate(turns):
        try:
            result = retrieve.retrieve(connection, turn, context=context)
        except Exception as error:  
            log(f"  [warn] 第 {index} 条检索失败：{error}")
            continue
        for candidate in result.candidates:
            hits[candidate.file_id] = hits.get(candidate.file_id, 0) + 1
    return hits


def workhorse_triggers(connection: sqlite3.Connection,
                       turns: list[str], limit: int = 15) -> list[tuple[str, int, str]]:
    ""
    rows = connection.execute(
        "SELECT t.keyword, t.file_id, f.name FROM triggers t "
        "JOIN files f ON f.id = t.file_id "
        "WHERE t.status='active' AND f.missing_since IS NULL").fetchall()
    counts = []
    for keyword, _file_id, name in rows:
        hits = sum(1 for turn in turns if keyword in turn)
        if hits:
            counts.append((keyword, hits, name))
    counts.sort(key=lambda item: (-item[1], item[0]))
    return counts[:limit]


def summarize(cards: list[FileCoverage]) -> dict:
    silent = [c for c in cards if c.silent]
    return {
        "files": len(cards),
        "silent": len(silent),
        "silent_names": [c.name for c in silent],
        "covered": len(cards) - len(silent),
        "total_hits": sum(c.trigger_hits for c in cards),
    }
