""

import argparse
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, render, retrieve, store


MARKER = render.MARKER_OPEN


def read_payload() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_prompt(payload: dict, fallback: str | None) -> str:
    if fallback:
        return fallback
    for key in ("prompt", "user_prompt", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_session_id(payload: dict, fallback: str | None) -> str | None:
    ""
    if fallback:
        return fallback
    value = payload.get("session_id")
    return value if isinstance(value, str) and value.strip() else None


def open_readonly(db_path: Path) -> sqlite3.Connection:
    ""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.3)
    connection.execute("PRAGMA busy_timeout=300")
    connection.execute("PRAGMA query_only=ON")
    return connection


def build_block(prompt: str, config: dict, connection: sqlite3.Connection,
                *, use_embedder: bool, no_dedup: bool,
                session_id: str | None = None) -> tuple[str, list]:
    embedder = None
    if use_embedder:
        from memlib import embed
        embedder = embed.get_embedder(config)

    context = retrieve.build_context(config, connection, embedder)
    result = retrieve.retrieve(connection, prompt, context=context)

    candidates = result.candidates
    if not no_dedup:
        window = int(store.get_meta(connection, "dedup_window_hours", "6"))
        already = store.recently_injected(connection, window, session_id)
        candidates = [c for c in candidates if c.file_id not in already]

    block = render.render(candidates, config, connection)
    render.assert_within_budget(block, connection)
    return block, result


def emit(block: str, raw: bool) -> None:
    if raw:
        sys.stdout.write(block + "\n")
    else:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": block,
                },
                "suppressOutput": True,
            },
            sys.stdout,
            ensure_ascii=False,
        )
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="UserPromptSubmit 记忆注入 hook。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--prompt", default=None,
                        help="干跑用：直接给一句话，不读 stdin")
    parser.add_argument("--raw", action="store_true", help="打印注入块本体而非 hook JSON")
    parser.add_argument("--no-embed", action="store_true", help="跳过向量阶段")
    parser.add_argument("--no-log", action="store_true", help="不写 injection_log（干跑必用）")
    parser.add_argument("--session-id", default=None,
                        help="干跑用：假装是这个会话，验会话级去重")
    parser.add_argument("--verbose", action="store_true", help="诊断信息走 stderr")
    args = parser.parse_args()

    def note(message: str) -> None:
        if args.verbose:
            print(message, file=sys.stderr)

    try:
        payload = read_payload()
        prompt = extract_prompt(payload, args.prompt)
        session_id = extract_session_id(payload, args.session_id)
        if len(prompt.strip()) < 2:
            note("prompt 太短，不注入")
            return
        if MARKER in prompt:
            note("prompt 里已有记忆索引标记（多半是历史重放），跳过")
            return

        config = db.load_config(args.config)
        db_path = args.db or db.resolve_path(config["db"]["path"])
        if not Path(db_path).exists():
            note(f"DB 不存在：{db_path}")
            return

        connection = open_readonly(Path(db_path))
        try:
            block, result = build_block(
                prompt, config, connection,
                use_embedder=not args.no_embed, no_dedup=args.no_log,
                session_id=session_id,
            )
            note(f"走过 {result.path_used}｜命中 {len(result.candidates)}｜"
                 f"跳过 {result.skipped}")
            if not block:
                note("无命中，沉默（04 §5.4：沉默比注入噪声好）")
                return
            emit(block, args.raw)
        finally:
            connection.close()

        if not args.no_log:
            log_injections(prompt, result, config, args, session_id)
    except Exception as error:  
        note(f"[降级] {type(error).__name__}: {error}")
        return


def log_injections(prompt: str, result, config: dict, args,
                   session_id: str | None = None) -> None:
    ""
    try:
        db_path = args.db or db.resolve_path(config["db"]["path"])
        connection = db.connect(db_path)
        try:
            digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            for candidate in render.pick_candidates(result.candidates, config, connection):
                store.log_injection(connection, digest, candidate.file_id,
                                    candidate.source, candidate.tier,
                                    session_id=session_id,
                                    route_ranks=result.ranks_of(candidate.file_id))
            connection.commit()
        finally:
            connection.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
