""

import argparse
from collections import Counter
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, llm, store
from memlib.text import normalize



STOPWORDS = {
    "项目", "配置", "工具", "文档", "记录", "内容", "系统", "问题", "东西", "相关",
    "记忆", "文件", "设置", "使用", "方法", "信息", "部分", "工作", "事情", "情况",
    "功能", "代码", "数据", "结果", "计划", "想法", "经验", "笔记", "学习", "记录",
    
    "full", "test", "main", "new", "old", "file", "files", "doc", "docs", "note",
    "list", "item", "part", "first", "next", "data", "info", "type", "name", "size",
    "high", "low", "more", "less", "when", "what", "this", "that", "with", "from",
    "over", "and", "the", "for", "can", "use", "all", "any", "get", "set", "run",
    "log", "logs", "text", "time", "date", "path", "user", "step", "mode",
}
MAX_FANOUT = 3          
MIN_LEN, MAX_LEN = 2, 6
MAX_ASCII_LEN = 24      
MIN_ASCII_LEN = 3       
MAX_PER_FILE = 5        




GENERIC_TAILS = (
    "学习", "编辑", "修改", "步骤", "脚本", "记录", "笔记", "配置", "设置", "流程",
    "成本", "陷阱", "问题", "方法", "工具", "教程", "清单", "模板", "方案", "调研",
    "实现", "实践", "体系", "架构", "复盘", "总结", "分析",
)
GENERIC_HEADS = ("关于", "我的", "这个", "那个")

PROMPT = """你在给一个外挂记忆系统生成"检索触发词"。

用户会在对话里打这些词，系统用**精确子串匹配**决定把哪个记忆文件推给他。
词越短越容易命中。

文件：{stem}
摘要：{description}

要求：
- 给 3-5 个词
- 中文词 2-4 个字
- 英文用**完整的单词或缩写**（WSL / Docker / logisim）。
  绝对不要把英文单词拆成片段：「astrbot」不能拆成「astr」「bot」，「dafeiyu」不能拆成「dafei」
- 必须是用户会打出来的**最短名词形式**，不要动词短语、不要描述句
- 不要加"学习/编辑/修改/步骤/清单"这类后缀，也不要把两个词拼起来
- 反例：「清除水印」太长，应该是「水印」；「纳新ppt」应该是「纳新」
- 每个词要对应**不同的说法入口**（口语词 / 简称 / 英文名），
  **不要给只是词序不同的排列组合**
- 不要"项目""配置""工具"这种放哪个文件都行的词
- 只输出 JSON：{{"triggers": ["...", "..."]}}"""


def build_prompt(stem: str, description: str) -> str:
    return PROMPT.format(stem=stem, description=description or "（无摘要）")


def _is_cjk(char: str) -> bool:
    return "一" <= char <= "鿿"


def derive_short_forms(word: str) -> list[str]:
    ""
    forms = [word]
    for tail in GENERIC_TAILS:
        if word.endswith(tail) and len(word) - len(tail) >= MIN_LEN:
            forms.append(word[:-len(tail)])
    for head in GENERIC_HEADS:
        if word.startswith(head) and len(word) - len(head) >= MIN_LEN:
            forms.append(word[len(head):])

    head_cjk = ""
    for char in word:
        if not _is_cjk(char):
            break
        head_cjk += char
    tail_cjk = ""
    for char in reversed(word):
        if not _is_cjk(char):
            break
        tail_cjk = char + tail_cjk

    if head_cjk == word and len(word) >= 4:
        
        forms.extend([word[:MIN_LEN], word[-MIN_LEN:]])
    else:
        
        if MIN_LEN <= len(head_cjk) < len(word):
            forms.append(head_cjk)
        if MIN_LEN <= len(tail_cjk) < len(word):
            forms.append(tail_cjk)
        prefix = word[:len(word) - len(tail_cjk)] if tail_cjk else ""
        if len(prefix) >= MIN_ASCII_LEN and prefix != word:
            forms.append(prefix)
    return forms


def gate(word: str, own_text: str, corpus_text: dict[int, str],
         accepted: list[str]) -> tuple[bool, str]:
    ""
    if not word:
        return False, "空词"
    if any(char.isspace() for char in word):
        return False, "含空白"
    if len(word) < MIN_LEN:
        return False, f"太短（{len(word)} 字，最少 {MIN_LEN}）"
    limit = MAX_ASCII_LEN if word.isascii() else MAX_LEN
    if len(word) > limit:
        return False, f"太长（{len(word)}，上限 {limit}）"
    if word in STOPWORDS:
        return False, "无区分度的通用词"
    if len(accepted) >= MAX_PER_FILE:
        return False, f"名额已满（每个文件最多 {MAX_PER_FILE} 个）"
    if word.isascii() and len(word) < MIN_ASCII_LEN:
        return False, f"英文碎片（{len(word)} 字符，最少 {MIN_ASCII_LEN}）"

    hits = sum(1 for text in corpus_text.values() if word in text)
    if hits > MAX_FANOUT:
        return False, f"撞车：命中 {hits} 个文件（上限 {MAX_FANOUT}）"

    
    if word.isascii() and hits:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])")
        if not any(pattern.search(text) for text in corpus_text.values()):
            return False, "英文碎片（只是更长单词的一部分）"

    
    
    
    
    if hits and word not in own_text:
        return False, f"不属于本文件（{hits} 个别的文件正文里有它）"

    
    
    subsumed = next((short for short in accepted if short in word), None)
    if subsumed:
        return False, f"被更短的「{subsumed}」覆盖（短词命中面更大）"

    
    permuted = next((kept for kept in accepted if sorted(kept) == sorted(word)), None)
    if permuted:
        return False, f"与「{permuted}」只是词序不同"

    return True, ""


def corpus_texts(connection) -> dict[int, str]:
    texts = {}
    for row in store.list_files(connection):
        stem = Path(row["path"]).stem.replace("-", " ")
        texts[row["id"]] = normalize(f"{stem} {row['description'] or ''}")
    return texts


def run(connection, config: dict, client: llm.LlamaClient, *, limit: int = 0,
        force: bool = False, dry_run: bool = False, log) -> dict:
    rows = store.list_files(connection)
    texts = corpus_texts(connection)
    stats = {"files": 0, "skipped": 0, "accepted": 0, "rejected": 0, "failed": 0,
             "reject_reasons": Counter()}

    for row in rows[:limit] if limit else rows:
        file_id = row["id"]
        existing = store.list_trigger_rows(connection, file_id)
        if any(source == "qwen" for _, source, _ in existing) and not force:
            stats["skipped"] += 1
            continue

        stats["files"] += 1
        stem = Path(row["path"]).stem
        payload = client.chat_json(build_prompt(stem, row["description"] or ""), max_tokens=300)
        if not payload or not isinstance(payload.get("triggers"), list):
            stats["failed"] += 1
            log(f"  [fail] {row['name']}: 模型没给出可用 JSON")
            continue

        own_text = texts[file_id]
        accepted: list[str] = []
        decided: list[tuple[str, bool, str]] = []
        raw_candidates = {
            normalize(str(item)) for item in payload["triggers"] if str(item).strip()
        }
        expanded = {form for word in raw_candidates for form in derive_short_forms(word)}
        candidates = sorted((word for word in expanded if word),
                            key=lambda word: (len(word), word))
        for word in candidates:
            if any(word == kept for kept, ok, _ in decided if ok):
                continue
            ok, reason = gate(word, own_text, texts, accepted)
            if ok:
                accepted.append(word)
            decided.append((word, ok, reason))

        log(f"  {row['name']}: 收 {accepted}｜拒 "
            f"{[w for w, ok, _ in decided if not ok]}")

        if dry_run:
            stats["accepted"] += len(accepted)
            stats["rejected"] += sum(1 for _, ok, _ in decided if not ok)
            continue

        
        
        store.retire_qwen_triggers(connection, file_id, "已被新一轮生成取代")
        for word, ok, reason in decided:
            store.upsert_trigger(connection, file_id, word, "qwen",
                                 "active" if ok else "rejected", reason)
            if ok:
                stats["accepted"] += 1
            else:
                stats["rejected"] += 1
                stats["reject_reasons"][reason.split("（")[0]] += 1
        connection.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="用 Qwen 批量生成触发词候选。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件（试跑用）")
    parser.add_argument("--force", action="store_true", help="已有 qwen 触发词的文件也重做")
    parser.add_argument("--dry-run", action="store_true")
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
        parser.exit(1, f"gen_triggers 失败：{error}\n")

    client = llm.get_client(config)
    if not llm.is_up(client):
        connection.close()
        parser.exit(1, f"筛选模型不可达：{client.endpoint}\n"
                       "先起 llama-server（见 docs/08 §10）\n")

    try:
        stats = run(connection, config, client, limit=args.limit, force=args.force,
                    dry_run=args.dry_run, log=log)
    finally:
        connection.close()

    import json
    result = dict(stats, reject_reasons=dict(stats["reject_reasons"]))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"处理 {stats['files']} 个文件（跳过 {stats['skipped']}）｜"
              f"采纳 {stats['accepted']}｜拒绝 {stats['rejected']}｜失败 {stats['failed']}")
        if stats["reject_reasons"]:
            print("拒绝原因：" + "｜".join(
                f"{reason} {count}" for reason, count in stats["reject_reasons"].most_common()))


if __name__ == "__main__":
    main()
