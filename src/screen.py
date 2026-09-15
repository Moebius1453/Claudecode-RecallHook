""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlib import db, dedup, llm, store


ROLE_LABEL = {"user": "用户", "assistant": "助手"}
VALID_CLASSES = ("fact", "rule", "project_state", "discard")

PROMPT = """你在给一个长期记忆系统从对话里挑出值得长期记住的东西。

判断标准：**一年后回头看，这条还起作用吗？**

值得记：
- 项目状态与进展、做过的决定及理由、踩过的坑、环境与配置、稳定的偏好
- 例：某个项目已经完成到什么程度、某个工具怎么装、某个 bug 的根因是什么

**不要记**（这一条最容易做错，宁可少记）：
- **一次性的请求**：{{"帮我看看这个文件"、"跑一下那个脚本"、"对这两个 PDF 评分"}} ——
  这是当次任务，不是长期知识。**除非它揭示了一个能复用的结论**。
- 寒暄、临时的调试过程、助手正在做什么的叙述

写**具体**：项目名、文件名、路径、命令、数字要照抄原文。
不要写"某个文件""那个项目""一些配置"这种**不指明对象**的说法。
摘要里说"用户"是可以的（本来就只有一个用户）。

分类：
- `fact` 事实 · `rule` 规矩/偏好 · `project_state` 项目状态 · `discard` 不值得记
- 判为 `discard` 时，**在 why 里写一句为什么判它不值得记**（方便事后抽查误判）。
  **这是合法且常见的结果，不要为了凑数硬编。**

对话：
{conversation}

只输出 JSON，不要解释、不要围栏：
{{"topic":"一句话主题","summary":"蒸馏后的内容，最多 200 字","why":"分类为 discard 时写判断理由，否则写为什么值得记，最多 50 字","classification":"fact|rule|project_state|discard"}}"""


def build_conversation(turns: list[dict]) -> str:
    lines = []
    for turn in turns:
        label = ROLE_LABEL.get(turn["role"], turn["role"])
        lines.append(f"[{label}] {turn['text']}")
    return "\n\n".join(lines)


def make_batches(turns: list[dict], batch_turns: int,
                 batch_max_chars: int) -> list[list[dict]]:
    ""
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for turn in turns:
        length = len(turn["text"]) + 8
        if current and (len(current) >= batch_turns or size + length > batch_max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(turn)
        size += length
    if current:
        batches.append(current)
    return batches


def parse_reply(reply) -> tuple[str, dict | None, str]:
    ""
    if not reply.ok:
        return "", None, f"{reply.kind}:{reply.raw[:80]}"
    data = reply.data
    classification = str(data.get("classification") or "").strip()
    if classification not in VALID_CLASSES:
        return "", None, f"分类非法:{classification[:40]}"
    return classification, data, ""


def run(connection, config, client, *, limit_batches: int = 0,
        dry_run: bool = False, log) -> dict:
    screen_cfg = config.get("screen", {})
    batch_turns = int(screen_cfg.get("batch_turns", 12))
    batch_max_chars = int(screen_cfg.get("batch_max_chars", 12000))
    max_retries = int(screen_cfg.get("max_retries", 3))
    topic_threshold = float(screen_cfg.get("dedup_topic_threshold", 0.30))
    full_threshold = float(screen_cfg.get("dedup_full_threshold", 0.10))

    stats = {"batches": 0, "turns": 0, "cards": 0, "discarded": 0, "duplicates": 0,
             "failed": 0, "abandoned": 0, "empty": 0, "malformed": 0}

    
    
    known = [dedup.Card(id=i, topic=t, summary=s)
             for i, t, s in store.canonical_cards(connection)]

    pending = store.pending_turns(connection)
    if not pending:
        return stats

    batches = make_batches(pending, batch_turns, batch_max_chars)
    if limit_batches:
        batches = batches[:limit_batches]

    for batch in batches:
        stats["batches"] += 1
        stats["turns"] += len(batch)
        ids = [turn["id"] for turn in batch]

        if dry_run:
            log(f"  [dry] {len(batch)} 轮 / {sum(len(t['text']) for t in batch)} 字")
            continue

        conversation = build_conversation(batch)
        reply = client.chat_json_reply(
            PROMPT.format(conversation=conversation), max_tokens=600)
        classification, card, reason = parse_reply(reply)

        if card is None:
            stats["failed"] += 1
            stats["empty" if reply.kind == "empty" else "malformed"] += 1
            attempts = store.bump_turn_attempts(connection, ids, reason)
            if attempts >= max_retries:
                
                
                
                store.abandon_turns(connection, ids, reason)
                stats["abandoned"] += 1
                log(f"  [放弃] {len(batch)} 轮试了 {attempts} 次：{reason}")
            else:
                log(f"  [失败] {len(batch)} 轮（第 {attempts} 次）：{reason}")
            connection.commit()
            continue

        topic = str(card.get("topic") or "").strip()[:120]
        summary = str(card.get("summary") or "").strip()[:400]
        why = str(card.get("why") or "").strip()[:120]
        if classification == "discard":
            card_id = store.add_card(
                connection, source_session=batch[0]["session_id"],
                ts=batch[0].get("ts"), topic=topic or "(判为不值得记)",
                summary=summary or reason or "(模型判为 discard)", snippet=None,
                why=why or None, classification="discard")
            store.mark_turns_screened(connection, ids, card_id, status="discarded")
            stats["discarded"] += 1
            log(f"  [discard] {len(batch)} 轮")
        else:
            card_id = store.add_card(
                connection, source_session=batch[0]["session_id"],
                ts=batch[0].get("ts"), topic=topic, summary=summary,
                snippet=batch[0]["text"][:500], why=why,
                classification=classification)
            store.mark_turns_screened(connection, ids, card_id)
            stats["cards"] += 1

            
            candidate_card = dedup.Card(id=card_id, topic=topic, summary=summary)
            dup_id, score, signal = dedup.find_duplicate(
                candidate_card, known,
                topic_threshold=topic_threshold, full_threshold=full_threshold)
            if dup_id is not None:
                store.set_card_duplicate(connection, card_id, dup_id)
                stats["duplicates"] += 1
                log(f"  [{classification}] {topic[:40]}"
                    f"  ↳ 与 #{dup_id} 重复（{signal} {score:.3f}）")
            else:
                known.append(candidate_card)
                
                log(f"  [{classification}] {topic[:40]}"
                    f"  （最近 {score:.3f}/{signal or '无'}）")
        connection.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="turns → 候选卡（本地 Qwen 筛选）。")
    parser.add_argument("--config", type=Path, default=db.DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit-batches", type=int, default=0, help="最多跑几批")
    parser.add_argument("--dry-run", action="store_true", help="只切批不调模型")
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
        parser.exit(1, f"screen 失败：{error}\n")

    client = llm.get_client(config)
    if not args.dry_run and not llm.is_up(client):
        connection.close()
        
        parser.exit(1, f"llama-server 没在跑（{client.endpoint}）。"
                       f"轮次会留在 turns 里等下次，不丢。\n启动见 08 §12。\n")

    try:
        stats = run(connection, config, client, limit_batches=args.limit_batches,
                    dry_run=args.dry_run, log=log)
    except Exception as error:  
        connection.rollback()
        parser.exit(1, f"screen 失败：{type(error).__name__}: {error}\n")
    finally:
        connection.close()

    if args.json:
        import json
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"批次 {stats['batches']}｜轮次 {stats['turns']}｜成卡 {stats['cards']}｜"
              f"discard {stats['discarded']}｜重复 {stats['duplicates']}｜失败 {stats['failed']}"
              f"（空回复 {stats['empty']} / 畸形 {stats['malformed']}）｜"
              f"放弃 {stats['abandoned']}"
              + ("（dry-run 未调用模型）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
