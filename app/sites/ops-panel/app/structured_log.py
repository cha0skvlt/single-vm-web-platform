"""LOG-1 structured NDJSON to stdout (ops-panel)."""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1"
SERVICE = os.environ.get("LOG_SERVICE", "ops-panel")
_LEVELS = frozenset({"debug", "info", "warn", "error"})
_stdout_lock = threading.Lock()


def _utc_ts() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def emit_log(
    level: str,
    event: str,
    msg: str,
    attrs: dict[str, Any] | None = None,
    *,
    trace_id: str | None = None,
) -> None:
    if level not in _LEVELS:
        raise ValueError(f"invalid level: {level}")
    line: dict[str, Any] = {
        "ts": _utc_ts(),
        "level": level,
        "service": SERVICE,
        "event": event,
        "msg": msg,
        "attrs": attrs or {},
        "schema_version": SCHEMA_VERSION,
    }
    if trace_id:
        line["trace_id"] = trace_id
    payload = json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _stdout_lock:
        sys.stdout.write(payload)
        sys.stdout.flush()
