""

import argparse
from pathlib import Path
import hashlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import corpus, db, embed, frontmatter, store
from memlib.vector import pack


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(connection, config: dict, *, dry_run: bool, prune: bool, reembed: bool,
        embedder, log) -> dict:
    docs, skipped = corpus.discover(config)
    stats = {"scanned": len(docs) + len(skipped), "indexed": len(docs), "added": 0,
             "updated": 0, "unchanged": 0, "skipped": len(skipped), "embedded": 0,
             "vector_pending": 0, "marked_missing": 0, "warnings": 0, "tfidf": 0}
    seen_paths: set[str] = set()

    for doc in docs:
        seen_paths.add(str(doc.path))
        meta = frontmatter.parse(doc.text, doc.path.stem)
        stats["warnings"] += len(meta.warnings)
        for warning in meta.warnings:
            log(f"  [warn] {doc.path.name}: {warning}")

        if dry_run:
            continue

        file_id, created, changed = store.upsert_doc(
            connection,
            path=str(doc.path),
            name=meta.name,
            description=meta.description,
            kind=meta.kind,
            status=meta.status,
            verified_at=meta.verified,
            content_hash=content_hash(doc.text),
            initial_tier=frontmatter.tier_for_kind(meta.kind),
        )
        stats["added" if created else ("updated" if changed else "unchanged")] += 1

        rejected = store.replace_triggers(connection, file_id, meta.triggers)
        for item in rejected:
            log(f"  [warn] {doc.path.name}: 触发词被拒 {item}")
        unresolved = store.replace_see_also(connection, file_id, meta.see_also)
        for name in unresolved:
            log(f"  [warn] {doc.path.name}: see_also 指向不存在的 name={name}")

    if prune and not dry_run:
        stats["marked_missing"] = store.mark_missing(connection, seen_paths)

    if reembed and not dry_run:
        connection.execute("UPDATE files SET desc_vector=NULL")

    stats["vector_pending"] = len(store.rows_needing_vector(connection, limit=0))

    if not dry_run and embedder is not None and embedder.available:
        
        previous = store.get_meta(connection, "embed_source")
        current = getattr(embedder, "endpoint", "") or embedder.model_id
        existing = connection.execute(
            "SELECT COUNT(*) FROM files WHERE desc_vector IS NOT NULL"
        ).fetchone()[0]
        if previous and previous != current and existing:
            log(f"[warn] 嵌入后端从 {previous} 换成 {current}，"
                f"库里 {existing} 条旧向量与新后端不可比 —— 建议 --reembed")

        pending = store.rows_needing_vector(connection, limit=0)
        batch = int(config.get("embed", {}).get("batch", 16))
        for start in range(0, len(pending), batch):
            chunk = pending[start:start + batch]
            vectors = embedder.embed([text for _, text in chunk])
            for (file_id, _), vector in zip(chunk, vectors):
                store.set_vector(connection, file_id, pack(vector))
                stats["embedded"] += 1
            connection.commit()
        stats["vector_pending"] = len(store.rows_needing_vector(connection, limit=0))
        store.set_meta(connection, "embed_model", embedder.model_id)
        store.set_meta(connection, "embed_dim", str(embedder.dim))
        store.set_meta(connection, "embed_source", current)

    if not dry_run:
        
        
        stats["tfidf"] = build_tfidf(connection)

    if not dry_run:
        connection.commit()
    return stats


def build_tfidf(connection) -> int:
    from memlib import tfidf
    from memlib.vector import pack

    rows = store.all_embed_texts(connection)
    if not rows:
        return 0
    token_sets = [tfidf.tokenize(text) for _, text in rows]
    idf = tfidf.build_idf(token_sets)
    for (file_id, text), _ in zip(rows, token_sets):
        store.set_tfidf_vector(connection, file_id, pack(tfidf.encode(text, idf)))
    store.set_meta(connection, "tfidf_idf", tfidf.pack_idf(idf))
    store.set_meta(connection, "tfidf_dim", str(tfidf.DIM))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="把 md 语料建成可检索索引。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--prune", action="store_true", help="标记消失的文件（不删行）")
    parser.add_argument("--reembed", action="store_true", help="换嵌入模型后全量重算")
    parser.add_argument("--no-embed", action="store_true", help="跳过向量阶段")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总")
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
        parser.exit(1, f"index_docs 失败：{error}\n")

    try:
        embedder = None
        if not args.no_embed:
            embedder = embed.get_embedder(config, bulk=True)
            if not embedder.available:
                log(f"[degrade] {embedder.reason} → 跳过向量阶段，其余照常")
        stats = run(connection, config, dry_run=args.dry_run, prune=args.prune,
                    reembed=args.reembed, embedder=embedder, log=log)
    except Exception as error:  
        parser.exit(1, f"index_docs 失败：{type(error).__name__}: {error}\n")
    finally:
        connection.close()

    if args.json:
        import json
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(
            f"扫描 {stats['scanned']}（入库 {stats['indexed']}，跳过 {stats['skipped']}）｜"
            f"新增 {stats['added']}｜更新 {stats['updated']}｜未变 {stats['unchanged']}｜"
            f"标记缺失 {stats['marked_missing']}｜已嵌入 {stats['embedded']}｜"
            f"待嵌入 {stats['vector_pending']}｜TF-IDF {stats['tfidf']}｜"
            f"告警 {stats['warnings']}"
        )


if __name__ == "__main__":
    main()
