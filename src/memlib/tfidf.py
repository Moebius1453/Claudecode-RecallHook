""

import base64
import math
import re
import zlib

from .text import normalize

DIM = 8192
NGRAM_SIZES = (2, 3)
_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]{1,}")
_CJK_RE = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    ""
    normalized = normalize(text)
    tokens: list[str] = []
    for word in _LATIN_RE.findall(normalized):
        tokens.append(word)
    for run in _CJK_RE.findall(normalized):
        for size in NGRAM_SIZES:
            for index in range(len(run) - size + 1):
                tokens.append(run[index:index + size])
    return tokens


def _bucket(token: str, dim: int) -> tuple[int, float]:
    ""
    value = zlib.crc32(token.encode("utf-8"))
    return value % dim, (1.0 if (value >> 20) & 1 else -1.0)


def _term_counts(tokens: list[str], dim: int) -> dict[int, float]:
    counts: dict[int, float] = {}
    for token in tokens:
        index, sign = _bucket(token, dim)
        counts[index] = counts.get(index, 0.0) + sign
    return counts


def _sublinear(value: float) -> float:
    magnitude = abs(value)
    if magnitude < 1.0:
        return 0.0
    return math.copysign(1.0 + math.log(magnitude), value)


def build_idf(documents: list[list[str]], dim: int = DIM) -> list[float]:
    ""
    total = len(documents)
    if total == 0:
        return [1.0] * dim
    df = [0] * dim
    for tokens in documents:
        for index in {_bucket(token, dim)[0] for token in tokens}:
            df[index] += 1
    return [math.log((1.0 + total) / (1.0 + count)) + 1.0 for count in df]


def encode(text: str, idf: list[float], dim: int = DIM) -> list[float]:
    ""
    counts = _term_counts(tokenize(text), dim)
    if not counts:
        return [0.0] * dim
    vector = [0.0] * dim
    for index, value in counts.items():
        weight = _sublinear(value)
        if weight:
            vector[index] = weight * idf[index]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def pack_idf(idf: list[float]) -> str:
    import array

    return base64.b64encode(array.array("f", idf).tobytes()).decode("ascii")


def unpack_idf(blob: str | None, dim: int = DIM) -> list[float]:
    import array

    if not blob:
        return [1.0] * dim
    raw = array.array("f")
    raw.frombytes(base64.b64decode(blob))
    values = list(raw)
    if len(values) != dim:
        return [1.0] * dim
    return values
