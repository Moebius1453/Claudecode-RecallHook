""

import re
import unicodedata


_WS = re.compile(r"\s+")
MIN_KEYWORD_LEN = 2


def normalize(text: str) -> str:
    ""
    text = unicodedata.normalize("NFKC", text or "")
    return _WS.sub(" ", text).strip().lower()


def normalize_keyword(keyword: str) -> str:
    return normalize(keyword)


def keyword_reject_reason(keyword: str) -> str | None:
    ""
    kw = normalize_keyword(keyword)
    if len(kw) < MIN_KEYWORD_LEN:
        return "单字触发词误命中严重，最少 2 字（见 03-classification-draft.md：白/黑 禁用）"
    if not kw:
        return "空触发词"
    return None


def ngrams(text: str, n: int = 3, limit: int = 0) -> list[str]:
    ""
    compact = normalize(text).replace(" ", "")
    if not compact:
        return []
    if len(compact) < n:
        return [compact]
    seen: dict[str, None] = {}
    for i in range(len(compact) - n + 1):
        gram = compact[i:i + n]
        if " " in gram:
            continue
        seen.setdefault(gram, None)
        if limit and len(seen) >= limit:
            break
    return list(seen)


def fts_query(grams: list[str]) -> str:
    ""
    return " OR ".join('"' + g.replace('"', '""') + '"' for g in grams if g)
