""

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from . import store



TIER_ORDER = ["archived", "resting", "warm", "always"]


def _step(tier: str, delta: int) -> str | None:
    if tier not in TIER_ORDER:
        return None
    index = TIER_ORDER.index(tier) + delta
    return TIER_ORDER[index] if 0 <= index < len(TIER_ORDER) else None


def next_up(tier: str) -> str | None:
    return _step(tier, 1)


def next_down(tier: str) -> str | None:
    return _step(tier, -1)


@dataclass(frozen=True)
class Change:
    file_id: int
    name: str
    old_tier: str
    new_tier: str
    reason: str          
    detail: str = ""     


def days_since(stamp: str | None, now: datetime | None = None) -> float | None:
    ""
    if not stamp:
        return None
    now = now or datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(stamp[:19], fmt).replace(tzinfo=timezone.utc)
            return (now - parsed).total_seconds() / 86400.0
        except ValueError:
            continue
    return None


def plan(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    ""
    window_days = int(store.get_meta(connection, "promotion_window_days", "30") or 30)
    min_injections = int(store.get_meta(connection, "promotion_min_injections", "5") or 5)
    warm_days = float(store.get_meta(connection, "warm_demotion_days", "90") or 90)
    resting_days = float(store.get_meta(connection, "resting_demotion_days", "180") or 180)
    cap = int(store.get_meta(connection, "always_cap", "15") or 15)

    rows = store.files_for_tier_review(connection)
    
    tiers = {row["id"]: row["tier"] for row in rows}
    changes: list[Change] = []
    stats = {"reviewed": len(rows), "pinned": 0, "never_injected": 0,
             "promoted": 0, "demoted": 0, "evicted": 0}

    
    
    frozen: set[int] = set()
    for row in rows:
        if row["pinned"]:
            frozen.add(row["id"])
            stats["pinned"] += 1
        elif not row["last_injected_at"]:
            frozen.add(row["id"])
            stats["never_injected"] += 1

    
    for row in rows:
        if row["id"] in frozen:
            continue
        if int(row["inject_count"] or 0) < min_injections:
            continue
        target = next_up(tiers[row["id"]])
        if target is None:
            continue
        changes.append(Change(row["id"], row["name"], tiers[row["id"]], target,
                              "frequency",
                              f"{window_days} 天内注入 {row['inject_count']} 次"))
        tiers[row["id"]] = target
        stats["promoted"] += 1

    
    always = [row for row in rows if tiers[row["id"]] == "always"]
    if len(always) > cap:
        
        always.sort(key=lambda row: (int(row["inject_count"] or 0),
                                     row["last_injected_at"] is not None,
                                     row["path"]))
        for row in always[: len(always) - cap]:
            changes.append(Change(row["id"], row["name"], "always", "warm",
                                  "cap_eviction",
                                  f"always 超编 {len(always)}/{cap}，用量最低"))
            tiers[row["id"]] = "warm"
            stats["evicted"] += 1

    
    for row in rows:
        if row["id"] in frozen:
            continue
        current = tiers[row["id"]]
        if current == "always":
            continue          
        age = days_since(row["last_injected_at"], now)
        if age is None:
            continue
        if current == "resting" and age > resting_days:
            changes.append(Change(row["id"], row["name"], current, "archived",
                                  "time", f"{age:.0f} 天未注入 > {resting_days:.0f}"))
            tiers[row["id"]] = "archived"
            stats["demoted"] += 1
        elif current == "warm" and age > warm_days:
            changes.append(Change(row["id"], row["name"], current, "resting",
                                  "time", f"{age:.0f} 天未注入 > {warm_days:.0f}"))
            tiers[row["id"]] = "resting"
            stats["demoted"] += 1

    return {
        "changes": changes,
        "stats": stats,
        "thresholds": {"promotion_window_days": window_days,
                       "promotion_min_injections": min_injections,
                       "warm_demotion_days": warm_days,
                       "resting_demotion_days": resting_days,
                       "always_cap": cap},
    }


def regrade(connection: sqlite3.Connection, *, apply: bool, log) -> dict:
    ""
    window_days = int(store.get_meta(connection, "promotion_window_days", "30") or 30)

    if not apply:
        connection.execute("SAVEPOINT regrade_dry")
        try:
            store.refresh_injection_stats(connection, window_days)
            result = plan(connection)
        finally:
            connection.execute("ROLLBACK TO regrade_dry")
            connection.execute("RELEASE regrade_dry")
        result["applied"] = 0
        return result

    store.refresh_injection_stats(connection, window_days)
    result = plan(connection)
    for change in result["changes"]:
        store.set_tier(connection, change.file_id, change.new_tier,
                       reason=change.reason, actor="auto")
        log(f"  {change.name}：{change.old_tier} → {change.new_tier}"
            f"（{change.reason}｜{change.detail}）")
    result["applied"] = len(result["changes"])
    return result
