""

import sqlite3

from . import vector as vec
from .text import normalize_keyword, keyword_reject_reason


FILE_COLUMNS = (
    "id, path, name, kind, status, tier, description, desc_vector, content_hash, "
    "pinned, last_injected_at, inject_count, verified_at, inject_mode, est_tokens, "
    "body_frozen, missing_since"
)
TIER_RANK = {"always": 0, "warm": 1, "resting": 2, "archived": 3}


TRIGGER_TIERS = ("always", "warm", "resting")
FUZZY_TIERS = ("warm",)


def _tier_clause(column: str, tiers: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{tier}'" for tier in tiers) + ")"

DEFAULT_META = {
    "warm_demotion_days": "90",
    "resting_demotion_days": "180",
    "promotion_window_days": "30",
    "promotion_min_injections": "5",
    "always_cap": "15",
    
    
    "always_line_chars": "120",
    "dedup_window_hours": "6",
    "injection_max_lines": "5",
    "injection_max_line_chars": "40",
    "injection_max_chars": "120",
    "discard_retention_days": "30",
    "vector_threshold": "0.55",
}


def row_to_dict(row: sqlite3.Row | tuple, columns: str = FILE_COLUMNS) -> dict:
    return dict(zip([name.strip() for name in columns.split(",")], row))


def qualified(alias: str = "f", columns: str = FILE_COLUMNS) -> str:
    ""
    return ", ".join(f"{alias}.{name.strip()}" for name in columns.split(","))


def get_meta(connection: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is not None:
        return row[0]
    return DEFAULT_META.get(key, default)


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )




def find_file_id(connection: sqlite3.Connection, path: str) -> int | None:
    row = connection.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    return row[0] if row else None


def find_file_by_name(connection: sqlite3.Connection, name: str) -> dict | None:
    row = connection.execute(
        f"SELECT {FILE_COLUMNS} FROM files WHERE name=? ORDER BY id LIMIT 1", (name,)
    ).fetchone()
    return row_to_dict(row) if row else None


def upsert_doc(
    connection: sqlite3.Connection,
    *,
    path: str,
    name: str,
    description: str,
    kind: str,
    status: str,
    verified_at: str | None,
    content_hash: str,
    initial_tier: str = "warm",
) -> tuple[int, bool, bool]:
    ""
    row = connection.execute(
        "SELECT id, content_hash FROM files WHERE path=?", (path,)
    ).fetchone()
    if row is None:
        cursor = connection.execute(
            "INSERT INTO files(path, name, description, kind, status, verified_at, "
            "content_hash, tier) VALUES(?,?,?,?,?,?,?,?)",
            (path, name, description, kind, status, verified_at, content_hash,
             initial_tier),
        )
        return int(cursor.lastrowid), True, True

    file_id, old_hash = int(row[0]), row[1]
    changed = old_hash != content_hash
    if changed:
        
        connection.execute(
            "UPDATE files SET name=?, description=?, kind=?, status=?, verified_at=?, "
            "content_hash=?, desc_vector=NULL, missing_since=NULL WHERE id=?",
            (name, description, kind, status, verified_at, content_hash, file_id),
        )
    else:
        connection.execute(
            "UPDATE files SET missing_since=NULL WHERE id=? AND missing_since IS NOT NULL",
            (file_id,),
        )
    return file_id, False, changed


def mark_missing(connection: sqlite3.Connection, seen_paths: set[str]) -> int:
    ""
    rows = connection.execute(
        "SELECT id, path FROM files WHERE missing_since IS NULL"
    ).fetchall()
    missing = [(int(row[0]),) for row in rows if row[1] not in seen_paths]
    if missing:
        connection.executemany(
            "UPDATE files SET missing_since=datetime('now') WHERE id=?", missing
        )
    return len(missing)


def list_files(
    connection: sqlite3.Connection,
    *,
    tier: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    missing_only: bool = False,
    limit: int = 0,
) -> list[dict]:
    clauses, params = [], []
    if tier:
        clauses.append("tier=?")
        params.append(tier)
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    if status:
        clauses.append("status=?")
        params.append(status)
    if missing_only:
        clauses.append("missing_since IS NOT NULL")
    sql = f"SELECT {FILE_COLUMNS} FROM files"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY tier, path"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [row_to_dict(row) for row in connection.execute(sql, params)]


def set_tier(connection: sqlite3.Connection, file_id: int, new_tier: str,
             reason: str, actor: str = "user") -> str | None:
    row = connection.execute("SELECT tier FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        return None
    old_tier = row[0]
    if old_tier == new_tier:
        return old_tier
    connection.execute("UPDATE files SET tier=? WHERE id=?", (new_tier, file_id))
    connection.execute(
        "INSERT INTO tier_events(file_id, old_tier, new_tier, reason, actor) VALUES(?,?,?,?,?)",
        (file_id, old_tier, new_tier, reason, actor),
    )
    return old_tier




def always_rows(connection: sqlite3.Connection) -> list[dict]:
    ""
    return [row_to_dict(row) for row in connection.execute(
        f"SELECT {FILE_COLUMNS} FROM files "
        "WHERE tier='always' AND missing_since IS NULL ORDER BY path"
    )]


def rows_without_tier_events(connection: sqlite3.Connection) -> list[dict]:
    ""
    return [row_to_dict(row) for row in connection.execute(
        f"SELECT {FILE_COLUMNS} FROM files "
        "WHERE missing_since IS NULL "
        "AND id NOT IN (SELECT file_id FROM tier_events) "
        "ORDER BY path"
    )]


def tier_event_counts(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM tier_events").fetchone()[0])




def replace_triggers(connection: sqlite3.Connection, file_id: int,
                     keywords: list[str]) -> list[str]:
    ""
    accepted, rejected = [], []
    for keyword in keywords:
        reason = keyword_reject_reason(keyword)
        if reason:
            rejected.append(f"{keyword}（{reason}）")
        else:
            accepted.append(normalize_keyword(keyword))
    connection.execute(
        "DELETE FROM triggers WHERE file_id=? AND source='frontmatter'", (file_id,)
    )
    connection.executemany(
        "INSERT OR IGNORE INTO triggers(file_id, keyword, source) "
        "VALUES(?,?,'frontmatter')",
        [(file_id, keyword) for keyword in dict.fromkeys(accepted)],
    )
    return rejected


def add_triggers(connection: sqlite3.Connection, file_id: int,
                 keywords: list[str]) -> tuple[list[str], list[str]]:
    added, rejected = [], []
    for keyword in keywords:
        reason = keyword_reject_reason(keyword)
        if reason:
            rejected.append(f"{keyword}（{reason}）")
            continue
        keyword = normalize_keyword(keyword)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO triggers(file_id, keyword, source) "
            "VALUES(?,?,'manual')",
            (file_id, keyword),
        )
        if cursor.rowcount:
            added.append(keyword)
    return added, rejected


def remove_triggers(connection: sqlite3.Connection, file_id: int,
                    keywords: list[str]) -> list[str]:
    removed = []
    for keyword in keywords:
        keyword = normalize_keyword(keyword)
        cursor = connection.execute(
            "DELETE FROM triggers WHERE file_id=? AND keyword=?", (file_id, keyword)
        )
        if cursor.rowcount:
            removed.append(keyword)
    return removed


def list_triggers(connection: sqlite3.Connection, file_id: int) -> list[str]:
    ""
    return [
        row[0] for row in connection.execute(
            "SELECT keyword FROM triggers WHERE file_id=? AND status='active' "
            "ORDER BY keyword", (file_id,)
        )
    ]


def list_trigger_rows(connection: sqlite3.Connection,
                      file_id: int) -> list[tuple[str, str, str]]:
    ""
    return [
        (row[0], row[1], row[2]) for row in connection.execute(
            "SELECT keyword, source, status FROM triggers WHERE file_id=? "
            "ORDER BY status, source, keyword",
            (file_id,),
        )
    ]


def retire_qwen_triggers(connection: sqlite3.Connection, file_id: int,
                         reason: str) -> int:
    ""
    cursor = connection.execute(
        "UPDATE triggers SET status='rejected', reason=? "
        "WHERE file_id=? AND source='qwen' AND status='active'",
        (reason, file_id),
    )
    return cursor.rowcount


def upsert_trigger(connection: sqlite3.Connection, file_id: int, keyword: str,
                   source: str, status: str, reason: str | None = None) -> None:
    connection.execute(
        "INSERT INTO triggers(file_id, keyword, source, status, reason) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(file_id, keyword) DO UPDATE SET "
        "status=excluded.status, reason=excluded.reason "
        "WHERE triggers.source=excluded.source",
        (file_id, keyword, source, status, reason),
    )


def trigger_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in connection.execute(
        "SELECT source, status, COUNT(*) FROM triggers GROUP BY source, status"
    ):
        counts[f"{row[0]}/{row[1]}"] = int(row[2])
    return counts


def rejected_triggers(connection: sqlite3.Connection, limit: int = 40) -> list[tuple[str, str, str]]:
    return [
        (row[0], row[1], row[2] or "") for row in connection.execute(
            "SELECT f.name, t.keyword, t.reason FROM triggers t JOIN files f ON f.id=t.file_id "
            "WHERE t.status='rejected' ORDER BY f.name, t.keyword LIMIT ?",
            (limit,),
        )
    ]


def files_without_triggers(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0] for row in connection.execute(
            "SELECT f.name FROM files f LEFT JOIN triggers t "
            "ON t.file_id=f.id AND t.status='active' "
            "WHERE t.keyword IS NULL AND f.missing_since IS NULL ORDER BY f.name"
        )
    ]




def replace_see_also(connection: sqlite3.Connection, file_id: int,
                     names: list[str]) -> list[str]:
    ""
    connection.execute("DELETE FROM see_also WHERE from_file_id=?", (file_id,))
    unresolved = []
    for name in dict.fromkeys(names):
        row = connection.execute("SELECT id FROM files WHERE name=?", (name,)).fetchone()
        if row is None:
            unresolved.append(name)
            continue
        connection.execute(
            "INSERT OR IGNORE INTO see_also(from_file_id, to_file_id) VALUES(?,?)",
            (file_id, int(row[0])),
        )
    return unresolved




def search_triggers(connection: sqlite3.Connection, normalized_prompt: str) -> list[dict]:
    ""
    rows = connection.execute(
        f"SELECT {qualified()}, t.keyword FROM triggers t JOIN files f ON f.id=t.file_id "
        f"WHERE {_tier_clause('f.tier', TRIGGER_TIERS)} AND f.missing_since IS NULL "
        "AND t.status='active' AND instr(?, t.keyword) > 0 "
        "ORDER BY f.tier, f.path",
        (normalized_prompt,),
    ).fetchall()
    results = []
    for row in rows:
        item = row_to_dict(row[:len(FILE_COLUMNS.split(","))])
        item["keyword"] = row[-1]
        results.append(item)
    return results


def search_fts(connection: sqlite3.Connection, match_query: str, limit: int = 20) -> list[tuple[dict, float]]:
    if not match_query:
        return []
    rows = connection.execute(
        f"SELECT {qualified()}, bm25(files_fts) AS rank FROM files_fts "
        "JOIN files f ON f.id = files_fts.rowid "
        f"WHERE files_fts MATCH ? AND {_tier_clause('f.tier', FUZZY_TIERS)} "
        "AND f.missing_since IS NULL "
        "ORDER BY rank, f.path LIMIT ?",
        (match_query, limit),
    ).fetchall()
    width = len(FILE_COLUMNS.split(","))
    return [(row_to_dict(row[:width]), -float(row[width])) for row in rows]


def retrievable_docs(connection: sqlite3.Connection) -> list[dict]:
    ""
    rows = connection.execute(
        f"SELECT {FILE_COLUMNS} FROM files "
        f"WHERE {_tier_clause('tier', FUZZY_TIERS)} AND missing_since IS NULL ORDER BY id"
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def vectors_for(connection: sqlite3.Connection, file_ids: list[int]) -> dict[int, bytes]:
    if not file_ids:
        return {}
    marks = ",".join("?" * len(file_ids))
    rows = connection.execute(
        f"SELECT id, desc_vector FROM files WHERE id IN ({marks}) "
        "AND desc_vector IS NOT NULL",
        list(file_ids),
    ).fetchall()
    return {int(row[0]): row[1] for row in rows}


def vector_rows(connection: sqlite3.Connection) -> list[tuple[dict, bytes]]:
    rows = connection.execute(
        f"SELECT {FILE_COLUMNS} FROM files WHERE desc_vector IS NOT NULL "
        f"AND {_tier_clause('tier', FUZZY_TIERS)} AND missing_since IS NULL ORDER BY id"
    ).fetchall()
    results = []
    for row in rows:
        item = row_to_dict(row)
        results.append((item, item.pop("desc_vector")))
    return results


def embed_text(path: str, description: str | None) -> str:
    ""
    from pathlib import Path

    stem = Path(path).stem.replace("-", " ")
    return f"{stem} {description or ''}".strip()


def rows_needing_vector(connection: sqlite3.Connection, limit: int = 0) -> list[tuple[int, str]]:
    sql = (
        "SELECT id, path, description FROM files WHERE desc_vector IS NULL "
        "AND description IS NOT NULL AND description <> '' ORDER BY id"
    )
    rows = connection.execute(sql).fetchall()
    results = [(int(r[0]), embed_text(r[1], r[2])) for r in rows]
    return results[:limit] if limit else results


def set_vector(connection: sqlite3.Connection, file_id: int, blob: bytes) -> None:
    connection.execute("UPDATE files SET desc_vector=? WHERE id=?", (blob, file_id))


def set_tfidf_vector(connection: sqlite3.Connection, file_id: int, blob: bytes) -> None:
    connection.execute("UPDATE files SET tfidf_vector=? WHERE id=?", (blob, file_id))


def tfidf_rows(connection: sqlite3.Connection) -> list[tuple[dict, bytes]]:
    ""
    width = len(FILE_COLUMNS.split(","))
    rows = connection.execute(
        f"SELECT {FILE_COLUMNS}, tfidf_vector FROM files "
        f"WHERE tfidf_vector IS NOT NULL AND {_tier_clause('tier', FUZZY_TIERS)} "
        "AND missing_since IS NULL ORDER BY id"
    ).fetchall()
    results = []
    for row in rows:
        item = row_to_dict(row[:width])
        item["tfidf_vector"] = row[width]
        results.append((item, row[width]))
    return results


def all_embed_texts(connection: sqlite3.Connection) -> list[tuple[int, str]]:
    ""
    rows = connection.execute(
        "SELECT id, path, description FROM files WHERE missing_since IS NULL ORDER BY id"
    ).fetchall()
    return [(int(row[0]), embed_text(row[1], row[2])) for row in rows]


def see_also_of(connection: sqlite3.Connection, file_id: int) -> list[dict]:
    rows = connection.execute(
        f"SELECT {qualified()} FROM files f JOIN see_also s ON s.to_file_id=f.id "
        "WHERE s.from_file_id=? AND f.missing_since IS NULL ORDER BY f.path",
        (file_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]




def recently_injected(connection: sqlite3.Connection, window_hours: int) -> set[int]:
    ""
    modifier = f"-{int(window_hours)} hours"
    rows = connection.execute(
        "SELECT DISTINCT file_id FROM injection_log "
        "WHERE file_id IS NOT NULL AND ts > datetime('now', ?)",
        (modifier,),
    ).fetchall()
    return {int(row[0]) for row in rows}


def log_injection(connection: sqlite3.Connection, prompt_hash: str, file_id: int,
                  source: str, tier_at_inject: str) -> None:
    connection.execute(
        "INSERT INTO injection_log(prompt_hash, file_id, source, tier_at_inject) "
        "VALUES(?,?,?,?)",
        (prompt_hash, file_id, source, tier_at_inject),
    )




def status(connection: sqlite3.Connection) -> dict:
    def scalar(sql: str, params=()) -> int:
        return int(connection.execute(sql, params).fetchone()[0])

    return {
        "files_total": scalar("SELECT COUNT(*) FROM files"),
        "files_missing": scalar("SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"),
        "files_no_description": scalar(
            "SELECT COUNT(*) FROM files WHERE description IS NULL OR description=''"
        ),
        "files_no_vector": scalar(
            "SELECT COUNT(*) FROM files WHERE desc_vector IS NULL AND description <> ''"
        ),
        "files_no_trigger": scalar(
            "SELECT COUNT(*) FROM files f LEFT JOIN triggers t "
            "ON t.file_id=f.id AND t.status='active' "
            "WHERE t.keyword IS NULL AND f.missing_since IS NULL"
        ),
        "triggers_total": scalar("SELECT COUNT(*) FROM triggers WHERE status='active'"),
        "triggers_by_source": trigger_counts(connection),
        "by_tier": {
            tier: scalar("SELECT COUNT(*) FROM files WHERE tier=?", (tier,))
            for tier in TIER_RANK
        },
        "by_kind": {
            row[0]: int(row[1]) for row in connection.execute(
                "SELECT kind, COUNT(*) FROM files GROUP BY kind ORDER BY kind"
            )
        },
        "cards_new": scalar("SELECT COUNT(*) FROM candidate_cards WHERE status='new'"),
        "schema_version": get_meta(connection, "schema_version"),
        "embed_model": get_meta(connection, "embed_model"),
    }
