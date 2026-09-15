CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'know-how'
        CHECK(kind IN ('rule','profile','project','know-how')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','waiting','resting','archived','deprecated')),
    tier TEXT NOT NULL DEFAULT 'warm'
        CHECK(tier IN ('always','warm','resting','archived')),
    description TEXT,
    desc_vector BLOB,
    content_hash TEXT,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    last_injected_at TEXT,
    inject_count INTEGER NOT NULL DEFAULT 0 CHECK(inject_count >= 0),
    verified_at TEXT,
    inject_mode TEXT NOT NULL DEFAULT 'pointer' CHECK(inject_mode IN ('pointer','none')),
    est_tokens INTEGER NOT NULL DEFAULT 0 CHECK(est_tokens >= 0),
    body_frozen INTEGER NOT NULL DEFAULT 0 CHECK(body_frozen IN (0,1)),
    missing_since TEXT,
    -- TF-IDF 兜底向量（特征哈希 1024 维）。embed-serve 挂掉时顶替 desc_vector。
    tfidf_vector BLOB
);

CREATE TABLE triggers (
    file_id INTEGER NOT NULL REFERENCES files(id),
    keyword TEXT NOT NULL,
    -- frontmatter = md 是真相，索引会用 md 覆盖这一部分
    -- manual      = memctl 手工加的，索引永不动
    -- qwen        = 本地模型生成的候选
    source TEXT NOT NULL DEFAULT 'frontmatter'
        CHECK(source IN ('frontmatter','manual','qwen')),
    -- rejected 不删行：被筛掉的候选要能复查（同 04 §4 的 discard 保留策略）
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','rejected')),
    reason TEXT,
    PRIMARY KEY (file_id, keyword)
);
CREATE INDEX idx_triggers_keyword ON triggers(keyword);
CREATE INDEX idx_triggers_status ON triggers(status);

CREATE TABLE see_also (
    from_file_id INTEGER NOT NULL REFERENCES files(id),
    to_file_id INTEGER NOT NULL REFERENCES files(id),
    PRIMARY KEY (from_file_id, to_file_id)
);

CREATE TABLE injection_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    prompt_hash TEXT NOT NULL,
    file_id INTEGER REFERENCES files(id),
    source TEXT CHECK(source IN ('always','trigger','ngram','fts','vector','see_also','manual')),
    tier_at_inject TEXT,
    -- 会话级去重的依据（08 §10.2-H2）。payload 的 session_id 实测存在，
    -- 见 docs/hook-payload-sample.json。旧行为 NULL。
    session_id TEXT,
    -- 每路名次的 JSON，如 {"trigger":0,"ngram":3}。不记这个就无法用证据调
    -- 1.0/0.8/0.6 的路权重和 0.50 的向量阈值（08 §10.2-H3）。
    route_ranks TEXT
);
CREATE INDEX idx_injlog_file_ts ON injection_log(file_id, ts);
CREATE INDEX idx_injlog_session ON injection_log(session_id, file_id);

CREATE TABLE tier_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    file_id INTEGER NOT NULL REFERENCES files(id),
    old_tier TEXT,
    new_tier TEXT NOT NULL,
    reason TEXT CHECK(reason IN ('time','frequency','pin','unpin','manual','cap_eviction')),
    actor TEXT NOT NULL CHECK(actor IN ('auto','user','agent'))
);

CREATE TABLE capture_state (
    source_path TEXT PRIMARY KEY,
    bytes_consumed INTEGER NOT NULL DEFAULT 0 CHECK(bytes_consumed >= 0),
    last_run TEXT
);

CREATE TABLE candidate_cards (
    id INTEGER PRIMARY KEY,
    source_session TEXT NOT NULL,
    ts TEXT,
    topic TEXT NOT NULL,
    summary TEXT NOT NULL,
    snippet TEXT,
    why TEXT,
    classification TEXT NOT NULL CHECK(classification IN ('fact','rule','project_state','discard')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','reviewed','written','discarded')),
    discarded_until TEXT,
    card_vector BLOB,
    source_kind TEXT NOT NULL DEFAULT 'text',
    noise_flags TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_cards_status ON candidate_cards(status);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO meta (key, value) VALUES
    ('schema_version', '10'),
    ('warm_demotion_days', '90'),
    ('resting_demotion_days', '180'),
    ('promotion_window_days', '30'),
    ('promotion_min_injections', '5'),
    ('always_cap', '15'),
    ('always_line_chars', '120'),
    ('dedup_window_hours', '6'),
    ('injection_max_lines', '5'),
    ('injection_max_line_chars', '60'),
    ('injection_max_chars', '345'),
    ('inject_warm_lines', '5'),
    ('inject_resting_lines', '2'),
    ('discard_retention_days', '30'),
    ('vector_threshold', '0.50');

CREATE VIRTUAL TABLE files_fts USING fts5(
    name, description, content='files', content_rowid='id', tokenize='trigram'
);
CREATE VIRTUAL TABLE cards_fts USING fts5(
    topic, summary, content='candidate_cards', content_rowid='id', tokenize='trigram'
);

CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name, description) VALUES(new.id, new.name, new.description);
END;
CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, description)
    VALUES('delete', old.id, old.name, old.description);
END;
CREATE TRIGGER files_au AFTER UPDATE OF id, name, description ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, description)
    VALUES('delete', old.id, old.name, old.description);
    INSERT INTO files_fts(rowid, name, description) VALUES(new.id, new.name, new.description);
END;
CREATE TRIGGER cards_ai AFTER INSERT ON candidate_cards BEGIN
    INSERT INTO cards_fts(rowid, topic, summary) VALUES(new.id, new.topic, new.summary);
END;
CREATE TRIGGER cards_ad AFTER DELETE ON candidate_cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, topic, summary)
    VALUES('delete', old.id, old.topic, old.summary);
END;
CREATE TRIGGER cards_au AFTER UPDATE OF id, topic, summary ON candidate_cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, topic, summary)
    VALUES('delete', old.id, old.topic, old.summary);
    INSERT INTO cards_fts(rowid, topic, summary) VALUES(new.id, new.topic, new.summary);
END;
