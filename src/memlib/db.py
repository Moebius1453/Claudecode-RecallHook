""

from pathlib import Path
import sqlite3
import tomllib


MAIN_DIR = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"
DEFAULT_CONFIG_PATH = MAIN_DIR / "config.toml"

SCHEMA_VERSION = 11


MIGRATIONS: dict[int, list[str]] = {
    3: ["ALTER TABLE files ADD COLUMN missing_since TEXT"],
    4: [
        
        
        "UPDATE meta SET value='60' WHERE key='injection_max_line_chars'",
        "UPDATE meta SET value='345' WHERE key='injection_max_chars'",
        "INSERT INTO meta(key,value) VALUES('inject_warm_lines','5') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        "INSERT INTO meta(key,value) VALUES('inject_resting_lines','2') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    ],
    5: [
        
        
        "ALTER TABLE triggers ADD COLUMN source TEXT NOT NULL DEFAULT 'frontmatter'",
    ],
    6: [
        
        """CREATE TABLE triggers_v6 (
            file_id INTEGER NOT NULL REFERENCES files(id),
            keyword TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'frontmatter'
                CHECK(source IN ('frontmatter','manual','qwen')),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','rejected')),
            reason TEXT,
            PRIMARY KEY (file_id, keyword)
        )""",
        "INSERT INTO triggers_v6(file_id, keyword, source) "
        "SELECT file_id, keyword, source FROM triggers",
        "DROP TABLE triggers",
        "ALTER TABLE triggers_v6 RENAME TO triggers",
        "CREATE INDEX idx_triggers_keyword ON triggers(keyword)",
        "CREATE INDEX idx_triggers_status ON triggers(status)",
    ],
    7: [
        
        
        """CREATE TABLE injection_log_v7 (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL DEFAULT (datetime('now')),
            prompt_hash TEXT NOT NULL,
            file_id INTEGER REFERENCES files(id),
            source TEXT CHECK(source IN ('always','trigger','ngram','fts','vector','see_also','manual')),
            tier_at_inject TEXT
        )""",
        "INSERT INTO injection_log_v7(id, ts, prompt_hash, file_id, source, tier_at_inject) "
        "SELECT id, ts, prompt_hash, file_id, source, tier_at_inject FROM injection_log",
        "DROP TABLE injection_log",
        "ALTER TABLE injection_log_v7 RENAME TO injection_log",
        "CREATE INDEX idx_injlog_file_ts ON injection_log(file_id, ts)",
    ],
    8: [
        
        "ALTER TABLE files ADD COLUMN tfidf_vector BLOB",
    ],
    9: [
        
        
        "INSERT INTO meta(key,value) VALUES('always_line_chars','120') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    ],
    10: [
        
        
        
        "ALTER TABLE injection_log ADD COLUMN session_id TEXT",
        "ALTER TABLE injection_log ADD COLUMN route_ranks TEXT",
        "CREATE INDEX idx_injlog_session ON injection_log(session_id, file_id)",
    ],
    11: [
        
        """CREATE TABLE turns (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            ts TEXT,
            uuid TEXT,
            text TEXT NOT NULL,
            text_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','screened','discarded')),
            card_id INTEGER REFERENCES candidate_cards(id),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            fail_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX idx_turns_status ON turns(status, id)",
        "CREATE INDEX idx_turns_session ON turns(session_id, id)",
    ],
}

REQUIRED_TABLES = {
    "files", "triggers", "see_also", "injection_log", "tier_events",
    "capture_state", "candidate_cards", "meta", "files_fts", "cards_fts", "turns",
}


class SchemaError(RuntimeError):
    pass


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    path = Path(path)
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    config["_path"] = path
    return config


def resolve_path(value: str | Path, base: Path = MAIN_DIR) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path)


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=5)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode != "wal":
            raise SchemaError(f"WAL 不可用：{mode}")
    except Exception:
        connection.close()
        raise
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _current_version(connection: sqlite3.Connection) -> int:
    if "meta" not in _table_names(connection):
        return 0
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _set_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(version),),
    )


def _exec_script(connection: sqlite3.Connection, script: str) -> None:
    ""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaError("schema.sql 存在不完整的语句")


def ensure_schema(db_path: str | Path, schema_path: Path = SCHEMA_PATH) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        version = _current_version(connection)
        if version == 0:
            _exec_script(connection, Path(schema_path).read_text(encoding="utf-8"))
            
            
            _set_version(connection, SCHEMA_VERSION)
        elif version < SCHEMA_VERSION:
            
            connection.execute("PRAGMA foreign_keys=OFF")
            for target in sorted(MIGRATIONS):
                if target <= version:
                    continue
                for statement in MIGRATIONS[target]:
                    connection.execute(statement)
                _set_version(connection, target)
            connection.execute("PRAGMA foreign_keys=ON")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise SchemaError(f"迁移后外键校验失败：{violations[:3]}")
        elif version > SCHEMA_VERSION:
            raise SchemaError(
                f"DB schema v{version} 高于代码 v{SCHEMA_VERSION}，拒绝降级操作。"
            )
        missing = REQUIRED_TABLES - _table_names(connection)
        if missing:
            raise SchemaError(f"schema 不完整，缺表：{sorted(missing)}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return db_path
