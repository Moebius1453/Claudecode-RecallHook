""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json


TURN_TYPES = ("user", "assistant")



TEXT_BLOCK = "text"
DROP_BLOCK_TYPES = frozenset({"thinking", "tool_use", "tool_result", "document", "image"})














NOISE_PREFIXES = (
    "[request interrupted by user",
    "<task-notification>",
    "<ide_opened_file>",
    "<ide_selection>",
    "<command-message>",
    "this session is being continued",
    "<command-name>",
    "<local-command",
    "<system-reminder>",
    "caveat: the messages below",
)

DEFAULT_MAX_TEXT_CHARS = 8000
DEFAULT_KEEP_CHARS = 2000


@dataclass(frozen=True)
class Turn:
    session_id: str
    role: str          
    text: str
    ts: str
    uuid: str
    source_path: str

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class ScanStats:
    lines: int = 0
    parsed: int = 0
    bad_json: int = 0
    skipped_type: int = 0
    skipped_sidechain: int = 0
    skipped_meta: int = 0
    skipped_noise: int = 0
    truncated: int = 0
    turns: int = 0


def truncate(text: str, max_chars: int = DEFAULT_MAX_TEXT_CHARS,
             keep: int = DEFAULT_KEEP_CHARS) -> tuple[str, bool]:
    ""
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    head, tail = text[:keep], text[-keep:]
    dropped = len(text) - len(head) - len(tail)
    return f"{head}\n[…中间省略 {dropped} 字…]\n{tail}", True


def blocks_to_text(content) -> str | None:
    ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != TEXT_BLOCK:
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts) if parts else None


def is_noise(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return any(lowered.startswith(prefix) for prefix in NOISE_PREFIXES)


def extract_turns(obj: dict, source_path: str, *,
                  include_sidechain: bool = False,
                  max_text_chars: int = DEFAULT_MAX_TEXT_CHARS) -> tuple[list[Turn], str]:
    ""
    kind = obj.get("type")
    if kind not in TURN_TYPES:
        return [], "type"
    if obj.get("isSidechain") and not include_sidechain:
        return [], "sidechain"
    if obj.get("isMeta"):
        return [], "meta"

    content = (obj.get("message") or {}).get("content")
    text = blocks_to_text(content)
    if text is None or is_noise(text):
        return [], "noise"

    text, _ = truncate(text, max_text_chars)

    
    
    from .render import strip_block
    text = strip_block(text)
    if not text.strip():
        return [], "noise"

    turn = Turn(
        session_id=str(obj.get("sessionId") or ""),
        role="user" if kind == "user" else "assistant",
        text=text,
        ts=str(obj.get("timestamp") or ""),
        uuid=str(obj.get("uuid") or ""),
        source_path=source_path,
    )
    return [turn], ""


def read_from(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    ""
    records: list[dict] = []
    consumed = offset
    with path.open("rb") as handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break          
            consumed += len(raw)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                records.append({"_bad_json": True})
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records, consumed


def scan(path: Path, offset: int = 0, *, include_sidechain: bool = False,
         max_text_chars: int = DEFAULT_MAX_TEXT_CHARS) -> tuple[list[Turn], int, ScanStats]:
    ""
    records, consumed = read_from(path, offset)
    stats = ScanStats(lines=len(records))
    turns: list[Turn] = []
    reasons = {"sidechain": "skipped_sidechain", "meta": "skipped_meta",
               "noise": "skipped_noise", "type": "skipped_type"}
    for obj in records:
        if obj.get("_bad_json"):
            stats.bad_json += 1
            continue
        stats.parsed += 1
        found, reason = extract_turns(obj, str(path), include_sidechain=include_sidechain,
                                      max_text_chars=max_text_chars)
        if reason:
            setattr(stats, reasons[reason], getattr(stats, reasons[reason]) + 1)
            continue
        turns.extend(found)
    stats.turns = len(turns)
    return turns, consumed, stats
