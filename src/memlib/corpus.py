""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Doc:
    path: Path          
    label: str          
    priority: int       
    text: str


@dataclass
class Skipped:
    path: Path
    reason: str


def enabled_roots(config: dict) -> list[dict]:
    roots = [dict(entry) for entry in config.get("corpus", []) if entry.get("enabled")]
    return sorted(roots, key=lambda entry: (entry.get("priority", 100), entry.get("label", "")))


def discover(config: dict) -> tuple[list[Doc], list[Skipped]]:
    docs: list[Doc] = []
    skipped: list[Skipped] = []
    skip_names = set(config.get("corpus_options", {}).get("skip_names", []))
    seen: set[Path] = set()

    for root in enabled_roots(config):
        root_path = Path(root["path"])
        label = root.get("label", root_path.name)
        priority = int(root.get("priority", 100))
        if not root_path.is_dir():
            skipped.append(Skipped(root_path, "语料根本身不存在"))
            continue
        for path in sorted(root_path.rglob("*.md")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.name in skip_names:
                skipped.append(Skipped(path, "在 skip_names 里"))
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                skipped.append(Skipped(path, f"读取失败：{error}"))
                continue
            docs.append(Doc(path=resolved, label=label, priority=priority, text=text))

    return docs, skipped
