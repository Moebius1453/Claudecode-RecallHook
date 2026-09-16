""

from collections import Counter
from dataclasses import dataclass
import re
import sqlite3

from . import corpus, store
from .retrieve import Candidate





MARKER_OPEN = "[记忆索引]"
MARKER_CLOSE = "[/记忆索引]"
_BLOCK_RE = re.compile(re.escape(MARKER_OPEN) + r".*?" + re.escape(MARKER_CLOSE), re.S)


@dataclass(frozen=True)
class Budgets:
    max_lines: int
    max_line_chars: int
    max_chars: int
    tier_lines: dict

    def lines_for(self, tier: str) -> int:
        return self.tier_lines.get(tier, 0)


def load_budgets(connection: sqlite3.Connection) -> Budgets:
    def number(key: str, fallback: int) -> int:
        return int(store.get_meta(connection, key, str(fallback)))

    return Budgets(
        max_lines=number("injection_max_lines", 5),
        max_line_chars=number("injection_max_line_chars", 60),
        max_chars=number("injection_max_chars", 345),
        tier_lines={
            "warm": number("inject_warm_lines", 5),
            "resting": number("inject_resting_lines", 2),
            
            "always": 0,
            "archived": 0,
        },
    )


def label_for(path: str, config: dict) -> str:
    ""
    from pathlib import Path

    target = Path(path)
    for root in corpus.enabled_roots(config):
        root_path = Path(root["path"])
        try:
            relative = target.relative_to(root_path)
        except ValueError:
            continue
        return f"{root.get('label', root_path.name)}/{relative.as_posix()}"
    return target.name


def build_header(config: dict) -> str:
    ""
    parts = [
        f"{root.get('label', root['path'])}={root['path']}"
        for root in corpus.enabled_roots(config)
    ]
    return ("[记忆索引] 以下为检索猜的线索，未必相关；不确定时去对应 md 确认（根目录 "
            + "｜".join(parts) + "）：")


def clip(text: str, limit: int) -> str:
    ""
    if limit <= 1:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]

    
    for opener, closer in (("（", "）"), ("(", ")")):
        if cut.count(opener) > cut.count(closer):
            index = cut.rfind(opener)
            if index >= 6:
                return cut[:index].rstrip() + "…"

    for boundary in (" ", "、", "，", ",", "；", ";", "/", "—", "-", "_"):
        index = cut.rfind(boundary)
        if index >= max(6, limit - 10):
            return cut[:index].rstrip() + "…"
    return cut.rstrip() + "…"


def format_line(candidate: Candidate, config: dict, max_line_chars: int) -> str:
    label = label_for(candidate.path, config)
    description = (candidate.description or "").strip()
    if not description:
        return clip(label, max_line_chars)
    room = max_line_chars - len(label) - 3
    if room < 6:
        
        return clip(label, max_line_chars)
    return f"{label} — {clip(description, room)}"


def pick(candidates: list[Candidate], config: dict,
         connection: sqlite3.Connection) -> list[tuple[Candidate, str]]:
    ""
    if not candidates:
        return []
    budgets = load_budgets(connection)
    
    used = len(build_header(config)) + len(MARKER_CLOSE) + 1
    used_by_tier: Counter = Counter()
    picked: list[tuple[Candidate, str]] = []

    for candidate in candidates:
        if len(picked) >= budgets.max_lines:
            break
        if used_by_tier[candidate.tier] >= budgets.lines_for(candidate.tier):
            continue
        line = format_line(candidate, config, budgets.max_line_chars)
        if not line:
            continue
        if used + 1 + len(line) > budgets.max_chars:
            continue
        picked.append((candidate, line))
        used_by_tier[candidate.tier] += 1
        used += len(line) + 1
    return picked


def render(candidates: list[Candidate], config: dict,
           connection: sqlite3.Connection) -> str:
    picked = pick(candidates, config, connection)
    if not picked:
        return ""
    lines = [build_header(config)]
    lines += [line for _, line in picked]
    lines.append(MARKER_CLOSE)
    return "\n".join(lines)


def pick_candidates(candidates: list[Candidate], config: dict,
                    connection: sqlite3.Connection) -> list[Candidate]:
    return [candidate for candidate, _ in pick(candidates, config, connection)]


def body_lines(block: str) -> list[str]:
    ""
    if not block:
        return []
    lines = block.splitlines()
    if lines and lines[0].startswith(MARKER_OPEN):
        lines = lines[1:]
    if lines and lines[-1] == MARKER_CLOSE:
        lines = lines[:-1]
    return lines


def strip_block(text: str) -> str:
    ""
    if not text or MARKER_OPEN not in text:
        return text
    cleaned = _BLOCK_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def has_block(text: str) -> bool:
    return bool(text) and MARKER_OPEN in text and MARKER_CLOSE in text


def assert_within_budget(block: str, connection: sqlite3.Connection) -> None:
    ""
    budgets = load_budgets(connection)
    body = body_lines(block)
    if not body:
        return
    if len(body) > budgets.max_lines:
        raise AssertionError(f"注入块 {len(body)} 行，超过上限 {budgets.max_lines}")
    if len(block) > budgets.max_chars:
        raise AssertionError(f"注入块 {len(block)} 字，超过上限 {budgets.max_chars}")
    for line in body:
        if len(line) > budgets.max_line_chars:
            raise AssertionError(f"单行 {len(line)} 字超过 {budgets.max_line_chars}：{line}")
    if not block.rstrip().endswith(MARKER_CLOSE):
        raise AssertionError("注入块缺尾标记，将来剥不干净")
