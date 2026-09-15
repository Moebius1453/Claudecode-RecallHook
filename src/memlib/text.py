""

import re
import unicodedata


_WS = re.compile(r"\s+")
MIN_KEYWORD_LEN = 2



_TOKEN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+|[a-z0-9]+")


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
    seen: dict[str, None] = {}
    for token in _TOKEN.findall(normalize(text)):
        if token[0].isascii():
            pieces = [token]
        elif len(token) < n:
            pieces = [token]
        else:
            pieces = [token[i:i + n] for i in range(len(token) - n + 1)]
        for piece in pieces:
            seen.setdefault(piece, None)
            if limit and len(seen) >= limit:
                return list(seen)
    return list(seen)


def fts_query(grams: list[str]) -> str:
    ""
    return " OR ".join('"' + g.replace('"', '""') + '"' for g in grams if g)
