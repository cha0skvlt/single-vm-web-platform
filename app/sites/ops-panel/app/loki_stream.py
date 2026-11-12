"""LOGS-1 — poll Loki query_range and emit compact log events for SSE."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

_LABEL_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def loki_base_url() -> str:
    return os.environ.get("LOKI_URL", "http://loki:3100").rstrip("/")


def backfill_seconds() -> int:
    return _env_int("LOGS_TAIL_BACKFILL", 300)


def poll_interval_seconds() -> float:
    return float(_env_int("LOGS_POLL_INTERVAL", 2))


def query_limit() -> int:
    return max(1, min(_env_int("LOGS_QUERY_LIMIT", 200), 1000))


def seen_capacity() -> int:
    return max(100, _env_int("LOGS_SEEN_MAX", 5000))


_DEFAULT_LOGS_LEVEL = "warn"
# Threshold → levels shown (debug = no filter). critical treated like error (Alloy/Traefik).
_LEVELS_BY_THRESHOLD: dict[str, frozenset[str] | None] = {
    "debug": None,
    "info": frozenset({"info", "warn", "error", "critical"}),
    "warn": frozenset({"warn", "error", "critical"}),
    "error": frozenset({"error", "critical"}),
}


def normalize_logs_level(raw: str | None, *, default: str = _DEFAULT_LOGS_LEVEL) -> str:
    name = (raw or "").strip().lower() or default
    if name in _LEVELS_BY_THRESHOLD:
        return name
    return default


def logs_level_from_env() -> str:
    return normalize_logs_level(os.environ.get("LOGS_LEVEL"))


def levels_for_threshold(threshold: str) -> frozenset[str] | None:
    """Return allowed level labels, or None if threshold is debug (show all)."""
    return _LEVELS_BY_THRESHOLD[normalize_logs_level(threshold)]


def entry_matches_level(
    entry: dict[str, str],
    *,
    exact_level: str | None = None,
    threshold: str | None = None,
) -> bool:
    lv = entry.get("level", "").strip().lower()
    if not lv:
        return False
    if exact_level:
        return lv == exact_level.strip().lower()
    allowed = levels_for_threshold(threshold or _DEFAULT_LOGS_LEVEL)
    if allowed is None:
        return True
    return lv in allowed


def escape_label(value: str) -> str:
    cleaned = _LABEL_SAFE.sub("", value.strip())
    return cleaned or "unknown"


def build_logql(
    level: str | None = None,
    service: str | None = None,
    *,
    threshold: str | None = None,
) -> str:
    parts = []
    if service:
        parts.append(f'service="{escape_label(service)}"')
    else:
        parts.append('service=~".+"')
    if level:
        parts.append(f'level="{escape_label(level)}"')
    else:
        allowed = levels_for_threshold(threshold or logs_level_from_env())
        if allowed is not None:
            pattern = "|".join(sorted(allowed))
            parts.append(f'level=~"{pattern}"')
    return "{" + ",".join(parts) + "}"


def parse_log_line(line: str) -> dict[str, str] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    ts = obj.get("ts")
    service = obj.get("service")
    level = obj.get("level")
    msg = obj.get("msg")
    if not all(isinstance(v, str) and v.strip() for v in (ts, service, level, msg)):
        return None
    return {
        "ts": ts.strip(),
        "service": service.strip(),
        "level": level.strip(),
        "msg": msg.strip(),
    }


def extract_entries(payload: dict) -> list[dict[str, str]]:
    if payload.get("status") != "success":
        return []
    data = payload.get("data") or {}
    results = data.get("result") or []
    out: list[dict[str, str]] = []
    for stream in results:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") or {}
        for pair in stream.get("values") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            ts_ns = str(pair[0])
            line = pair[1] if isinstance(pair[1], str) else str(pair[1])
            entry = parse_log_line(line)
            if not entry:
                service = labels.get("service")
                level = labels.get("level")
                if isinstance(service, str) and isinstance(level, str):
                    entry = {
                        "ts": _ns_to_rfc3339(ts_ns),
                        "service": service,
                        "level": level,
                        "msg": line[:500],
                    }
                else:
                    continue
            entry["_ts_ns"] = ts_ns
            out.append(entry)
    out.sort(key=lambda e: int(e["_ts_ns"]))
    return out


def _ns_to_rfc3339(ts_ns: str) -> str:
    try:
        ns = int(ts_ns)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    sec = ns / 1_000_000_000
    dt = datetime.fromtimestamp(sec, tz=timezone.utc)
    ms = int((ns % 1_000_000_000) // 1_000_000)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def dedup_key(entry: dict[str, str]) -> tuple[str, str, str]:
    return (entry["ts"], entry["service"], entry["msg"])


def compact_entry(entry: dict[str, str]) -> dict[str, str]:
    return {
        "ts": entry["ts"],
        "service": entry["service"],
        "level": entry["level"],
        "msg": entry["msg"],
    }


def sse_data_line(entry: dict[str, str]) -> bytes:
    body = json.dumps(compact_entry(entry), separators=(",", ":"))
    return f"data: {body}\n\n".encode("utf-8")


@dataclass
class StreamState:
    cursor_ns: int
    seen: set[tuple[str, str, str]] = field(default_factory=set)

    def note(self, entry: dict[str, str]) -> None:
        key = dedup_key(entry)
        self.seen.add(key)
        try:
            self.cursor_ns = max(self.cursor_ns, int(entry["_ts_ns"]) + 1)
        except (KeyError, TypeError, ValueError):
            pass
        self.prune_seen()

    def prune_seen(self) -> None:
        cap = seen_capacity()
        while len(self.seen) > cap:
            self.seen.pop()


def initial_cursor_ns(backfill_sec: int | None = None) -> int:
    sec = backfill_sec if backfill_sec is not None else backfill_seconds()
    start = time.time() - sec
    return int(start * 1_000_000_000)


def query_range(
    *,
    logql: str,
    start_ns: int,
    end_ns: int | None = None,
    limit: int | None = None,
    base_url: str | None = None,
    timeout: float = 10,
) -> dict:
    end = end_ns if end_ns is not None else int(time.time() * 1_000_000_000)
    params = urllib.parse.urlencode(
        {
            "query": logql,
            "start": str(start_ns),
            "end": str(end),
            "direction": "forward",
            "limit": str(limit or query_limit()),
        }
    )
    url = f"{(base_url or loki_base_url())}/loki/api/v1/query_range?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        raise ValueError(f"loki query_range HTTP {exc.code}: {body}") from exc


def collect_sse_frames(
    state: StreamState,
    *,
    level: str | None = None,
    service: str | None = None,
    threshold: str | None = None,
    base_url: str | None = None,
    timeout: float = 10,
) -> list[bytes]:
    """One poll window → SSE payloads (for tests and the HTTP handler)."""
    return [
        sse_data_line(entry)
        for entry in poll_entries(
            state,
            level=level,
            service=service,
            threshold=threshold,
            base_url=base_url,
            timeout=timeout,
        )
    ]


def poll_entries(
    state: StreamState,
    *,
    level: str | None = None,
    service: str | None = None,
    threshold: str | None = None,
    base_url: str | None = None,
    timeout: float = 10,
) -> list[dict[str, str]]:
    eff_threshold = None if level else normalize_logs_level(threshold or logs_level_from_env())
    logql = build_logql(level, service, threshold=eff_threshold)
    try:
        payload = query_range(
            logql=logql,
            start_ns=state.cursor_ns,
            base_url=base_url,
            timeout=timeout,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return []
    fresh: list[dict[str, str]] = []
    for entry in extract_entries(payload):
        if not entry_matches_level(
            entry, exact_level=level, threshold=eff_threshold
        ):
            continue
        key = dedup_key(entry)
        if key in state.seen:
            continue
        state.note(entry)
        fresh.append(entry)
    return fresh
