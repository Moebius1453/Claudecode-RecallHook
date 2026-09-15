""

from dataclasses import dataclass, field
import yaml


TYPE_TO_KIND = {
    "user": "profile",
    "feedback": "rule",
    "project": "project",
    "reference": "know-how",
}



KIND_TO_TIER = {
    "rule": "always",
    "profile": "always",
    "project": "warm",
    "know-how": "warm",
}


def tier_for_kind(kind: str) -> str:
    return KIND_TO_TIER.get(kind, "warm")

VALID_KINDS = {"rule", "profile", "project", "know-how"}
VALID_STATUS = {"active", "waiting", "resting", "archived", "deprecated"}
MAX_TRIGGERS = 8


@dataclass
class Meta:
    name: str
    description: str
    kind: str
    status: str
    triggers: list[str] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)
    verified: str | None = None
    warnings: list[str] = field(default_factory=list)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return []


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    ""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    block = text[3:end]
    body_start = text.find("\n", end + 1)
    body = text[body_start + 1:] if body_start >= 0 else ""
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return None, text
    if not isinstance(data, dict):
        return None, text
    return data, body


def parse(text: str, fallback_name: str) -> Meta:
    data, _ = split_frontmatter(text)
    warnings: list[str] = []

    if data is None:
        return Meta(
            name=fallback_name,
            description="",
            kind="know-how",
            status="active",
            warnings=["无 frontmatter：name 取文件名，description 为空（无法参与向量召回）"],
        )

    name = str(data.get("name") or fallback_name)
    description = str(data.get("description") or "").strip()
    if not description:
        warnings.append("description 为空（无法参与向量召回）")

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    declared_kind = data.get("kind")
    if declared_kind in VALID_KINDS:
        kind = declared_kind
    else:
        raw_type = str(metadata.get("type") or "")
        kind = TYPE_TO_KIND.get(raw_type, "know-how")
        if raw_type and raw_type not in TYPE_TO_KIND:
            warnings.append(f"未知 metadata.type={raw_type!r}，按 know-how 处理")

    status = data.get("status")
    if status not in VALID_STATUS:
        if status is not None:
            warnings.append(f"未知 status={status!r}，按 active 处理")
        status = "active"

    triggers = _as_list(data.get("triggers"))
    if len(triggers) > MAX_TRIGGERS:
        warnings.append(f"触发词 {len(triggers)} 个，超过建议上限 {MAX_TRIGGERS}")

    verified = data.get("verified")
    return Meta(
        name=name,
        description=description,
        kind=kind,
        status=status,
        triggers=triggers,
        see_also=_as_list(data.get("see_also")),
        verified=str(verified) if verified else None,
        warnings=warnings,
    )


def render_frontmatter(meta: Meta) -> str:
    ""
    lines = ["---", f"name: {meta.name}", f"description: {meta.description}"]
    if meta.triggers:
        lines.append("triggers: [" + ", ".join(meta.triggers) + "]")
    if meta.see_also:
        lines.append("see_also: [" + ", ".join(meta.see_also) + "]")
    if meta.verified:
        lines.append(f"verified: {meta.verified}")
    lines.append("---")
    return "\n".join(lines)
