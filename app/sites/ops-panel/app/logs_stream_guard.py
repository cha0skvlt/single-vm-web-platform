"""Concurrency and duration limits for /logs/stream SSE."""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
_active_streams = 0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def max_concurrent_streams() -> int:
    return max(1, _env_int("LOGS_STREAM_MAX", 3))


def stream_max_duration_seconds() -> int:
    return max(60, _env_int("LOGS_STREAM_MAX_DURATION", 900))


def heartbeat_interval_seconds() -> int:
    return max(5, _env_int("LOGS_STREAM_HEARTBEAT", 15))


def try_acquire_stream() -> bool:
    global _active_streams
    with _lock:
        if _active_streams >= max_concurrent_streams():
            return False
        _active_streams += 1
        return True


def release_stream() -> None:
    global _active_streams
    with _lock:
        _active_streams = max(0, _active_streams - 1)


def reset_for_tests() -> None:
    global _active_streams
    with _lock:
        _active_streams = 0
