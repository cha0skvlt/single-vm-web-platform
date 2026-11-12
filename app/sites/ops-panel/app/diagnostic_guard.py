"""Single-flight guard and cooldown for /diagnostic POST."""

from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_busy = False
_last_finished = 0.0


def cooldown_seconds() -> int:
    return max(0, _env_int("DIAGNOSTIC_COOLDOWN", 60))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def try_begin() -> tuple[bool, int | None]:
    """Return (allowed, retry_after_seconds). retry_after set on cooldown only."""
    global _busy, _last_finished
    now = time.time()
    cooldown = cooldown_seconds()
    with _lock:
        if _busy:
            return False, None
        elapsed = now - _last_finished
        if cooldown and elapsed < cooldown:
            return False, max(1, int(cooldown - elapsed))
        _busy = True
        return True, None


def finish() -> None:
    global _busy, _last_finished
    with _lock:
        _busy = False
        _last_finished = time.time()


def reset_for_tests() -> None:
    global _busy, _last_finished
    with _lock:
        _busy = False
        _last_finished = 0.0
