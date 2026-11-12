"""FAIL2BAN tile — jail stats and recent bans from Loki (host fail2ban.log via Alloy)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
from typing import Any

from loki_stream import extract_entries, query_range

_BAN_EVENT_RE = re.compile(r"\[([^\]]+)\] (Ban|Unban) (\S+)")
_KNOWN_JAILS = ("sshd", "traefik-auth", "traefik-scan")
_RECENT_BANS = 5


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def lookback_hours() -> int:
    return max(1, _env_int("FAIL2BAN_LOOKBACK_HOURS", 168))


def threat_window_minutes() -> int:
    return max(1, _env_int("FAIL2BAN_THREAT_WINDOW_MIN", 15))


def threat_level(bans_in_window: int) -> str:
    if bans_in_window <= 1:
        return "ok"
    if bans_in_window <= 9:
        return "warn"
    return "crit"


def _format_ago(ts_ns: int, now_ns: int) -> str:
    sec = max(0, (now_ns - ts_ns) // 1_000_000_000)
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    return f"{sec // 3600}h ago"


def _line_text(entry: dict[str, str]) -> str:
    return entry.get("line") or entry.get("msg") or ""


def _parse_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for entry in extract_entries(payload):
        line = _line_text(entry)
        match = _BAN_EVENT_RE.search(line)
        if not match:
            continue
        ts_raw = entry.get("_ts_ns", "0")
        try:
            ts_ns = int(ts_raw)
        except (TypeError, ValueError):
            ts_ns = 0
        events.append(
            {
                "ts_ns": ts_ns,
                "jail": match.group(1),
                "action": match.group(2),
                "ip": match.group(3),
            }
        )
    events.sort(key=lambda e: e["ts_ns"])
    return events


def _bans_in_window(events: list[dict[str, Any]], window_sec: int, now_ns: int) -> int:
    cutoff = now_ns - window_sec * 1_000_000_000
    return sum(1 for ev in events if ev["action"] == "Ban" and ev["ts_ns"] >= cutoff)


def _active_bans(events: list[dict[str, Any]]) -> dict[str, set[str]]:
    active: dict[str, set[str]] = {}
    for ev in events:
        jail, ip = ev["jail"], ev["ip"]
        ips = active.setdefault(jail, set())
        if ev["action"] == "Ban":
            ips.add(ip)
        else:
            ips.discard(ip)
    return active


def _recent_bans(events: list[dict[str, Any]], now_ns: int, limit: int) -> list[dict[str, str]]:
    """One row per currently active (jail, ip), using the latest Ban time."""
    active = _active_bans(events)
    active_pairs = {(jail, ip) for jail, ips in active.items() for ip in ips}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if ev["action"] != "Ban":
            continue
        key = (ev["jail"], ev["ip"])
        if key not in active_pairs:
            continue
        prev = latest.get(key)
        if prev is None or ev["ts_ns"] >= prev["ts_ns"]:
            latest[key] = ev
    bans = sorted(latest.values(), key=lambda e: e["ts_ns"], reverse=True)
    out: list[dict[str, str]] = []
    for ev in bans[:limit]:
        out.append(
            {
                "ip": ev["ip"],
                "jail": ev["jail"],
                "ago": _format_ago(ev["ts_ns"], now_ns),
            }
        )
    return out


def _fetch_events() -> tuple[list[dict[str, Any]], int]:
    hours = lookback_hours()
    now_ns = int(time.time() * 1_000_000_000)
    start_ns = now_ns - hours * 3_600_000_000_000
    logql = '{service="fail2ban"} |~ "(Ban|Unban) "'
    payload = query_range(
        logql=logql,
        start_ns=start_ns,
        end_ns=now_ns,
        limit=5000,
    )
    return _parse_events(payload), now_ns


def _empty_payload(*, status: str, detail: str, reachable: bool) -> dict[str, Any]:
    return {
        "status": status,
        "reachable": reachable,
        "detail": detail,
        "banned_now": 0,
        "active_jails": 0,
        "jail_names": [],
        "bans_1h": 0,
        "recent": [],
    }


def _jail_stats_from_events(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    active = _active_bans(events)
    stats: dict[str, dict[str, int]] = {}
    for ev in events:
        jail = ev["jail"]
        row = stats.setdefault(jail, {"active": 0, "total": 0})
        if ev["action"] == "Ban":
            row["total"] += 1
    for jail, ips in active.items():
        row = stats.setdefault(jail, {"active": 0, "total": 0})
        row["active"] = len(ips)
    return stats


def fail2ban_payload() -> dict[str, Any]:
    """Structured metrics for FAIL2BAN tile (does not affect overall health)."""
    if not _env_bool("HEALTH_CHECK_FAIL2BAN", True):
        return _empty_payload(status="ok", detail="check disabled", reachable=True)

    try:
        events, now_ns = _fetch_events()
        recent = _recent_bans(events, now_ns, _RECENT_BANS)
        bans_1h = _bans_in_window(events, 3600, now_ns)
        level = threat_level(_bans_in_window(events, threat_window_minutes() * 60, now_ns))
        stats = _jail_stats_from_events(events)
        banned_now = sum(row["active"] for row in stats.values())
        jail_names = sorted(set(_KNOWN_JAILS) | set(stats.keys()))
        active_jails = len(jail_names)
        detail = f"{banned_now} banned now; {bans_1h} ban(s) in 1h"
        return {
            "status": level,
            "reachable": True,
            "detail": detail,
            "banned_now": banned_now,
            "active_jails": active_jails,
            "jail_names": jail_names,
            "bans_1h": bans_1h,
            "recent": recent,
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return _empty_payload(
            status="unknown",
            detail=f"metrics unavailable: {exc}",
            reachable=False,
        )
