"""LOGS-1 — Loki SSE log stream (ops-panel)."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

SITE_APP = Path(__file__).resolve().parents[2] / "app/sites/ops-panel/app"


def _load_loki_stream():
    path = SITE_APP / "loki_stream.py"
    name = "prod_loki_stream_unit"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_server(monkeypatch):
    monkeypatch.setenv("PROD_ADMIN_USER", "admin")
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "secret")
    auth_path = SITE_APP / "auth.py"
    auth_name = "prod_status_auth_sse"
    auth_spec = importlib.util.spec_from_file_location(auth_name, auth_path)
    auth_mod = importlib.util.module_from_spec(auth_spec)
    assert auth_spec.loader is not None
    sys.modules[auth_name] = auth_mod
    auth_spec.loader.exec_module(auth_mod)
    sys.modules["auth"] = auth_mod

    structured_path = SITE_APP / "structured_log.py"
    st_name = "prod_status_structured_log_sse"
    st_spec = importlib.util.spec_from_file_location(st_name, structured_path)
    st_mod = importlib.util.module_from_spec(st_spec)
    assert st_spec.loader is not None
    sys.modules[st_name] = st_mod
    st_spec.loader.exec_module(st_mod)
    sys.modules["structured_log"] = st_mod

    loki_path = SITE_APP / "loki_stream.py"
    loki_name = "loki_stream"
    loki_spec = importlib.util.spec_from_file_location(loki_name, loki_path)
    loki_mod = importlib.util.module_from_spec(loki_spec)
    assert loki_spec.loader is not None
    sys.modules[loki_name] = loki_mod
    loki_spec.loader.exec_module(loki_mod)

    sites_path = SITE_APP / "sites_status.py"
    sites_name = "sites_status"
    sites_spec = importlib.util.spec_from_file_location(sites_name, sites_path)
    sites_mod = importlib.util.module_from_spec(sites_spec)
    assert sites_spec.loader is not None
    sys.modules[sites_name] = sites_mod
    sites_spec.loader.exec_module(sites_mod)

    paths_path = SITE_APP / "install_paths.py"
    paths_spec = importlib.util.spec_from_file_location("install_paths", paths_path)
    paths_mod = importlib.util.module_from_spec(paths_spec)
    assert paths_spec.loader is not None
    sys.modules["install_paths"] = paths_mod
    paths_spec.loader.exec_module(paths_mod)

    for helper_name, helper_file in (
        ("request_limits", "request_limits.py"),
        ("fail2ban_status", "fail2ban_status.py"),
    ):
        helper_path = SITE_APP / helper_file
        helper_spec = importlib.util.spec_from_file_location(helper_name, helper_path)
        helper_mod = importlib.util.module_from_spec(helper_spec)
        assert helper_spec.loader is not None
        sys.modules[helper_name] = helper_mod
        helper_spec.loader.exec_module(helper_mod)

    health_path = SITE_APP / "health_status.py"
    health_name = "health_status"
    health_spec = importlib.util.spec_from_file_location(health_name, health_path)
    health_mod = importlib.util.module_from_spec(health_spec)
    assert health_spec.loader is not None
    sys.modules[health_name] = health_mod
    health_spec.loader.exec_module(health_mod)

    for helper_name, helper_file in (
        ("request_limits", "request_limits.py"),
        ("diagnostic_guard", "diagnostic_guard.py"),
        ("logs_stream_guard", "logs_stream_guard.py"),
    ):
        helper_path = SITE_APP / helper_file
        helper_spec = importlib.util.spec_from_file_location(helper_name, helper_path)
        helper_mod = importlib.util.module_from_spec(helper_spec)
        assert helper_spec.loader is not None
        sys.modules[helper_name] = helper_mod
        helper_spec.loader.exec_module(helper_mod)
        if hasattr(helper_mod, "reset_for_tests"):
            helper_mod.reset_for_tests()

    server_path = SITE_APP / "server.py"
    srv_name = "prod_status_server_sse"
    srv_spec = importlib.util.spec_from_file_location(srv_name, server_path)
    srv_mod = importlib.util.module_from_spec(srv_spec)
    assert srv_spec.loader is not None
    sys.modules[srv_name] = srv_mod
    srv_spec.loader.exec_module(srv_mod)
    return srv_mod, auth_mod, loki_mod


LOKI_FIXTURE = {
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {"service": "ops-panel", "level": "info"},
                "values": [
                    [
                        "1700000000000000001",
                        json.dumps(
                            {
                                "ts": "2024-01-01T00:00:00.001Z",
                                "service": "ops-panel",
                                "level": "info",
                                "msg": "first",
                                "event": "test.one",
                                "attrs": {},
                                "schema_version": "1",
                            }
                        ),
                    ],
                    [
                        "1700000000000000002",
                        json.dumps(
                            {
                                "ts": "2024-01-01T00:00:00.002Z",
                                "service": "ops-panel",
                                "level": "warn",
                                "msg": "second",
                                "event": "test.two",
                                "attrs": {},
                                "schema_version": "1",
                            }
                        ),
                    ],
                ],
            }
        ],
    },
}


@pytest.mark.unit
def test_build_logql_filters():
    mod = _load_loki_stream()
    assert mod.build_logql(threshold="debug") == '{service=~".+"}'
    assert (
        mod.build_logql(threshold="warn")
        == '{service=~".+",level=~"critical|error|warn"}'
    )
    assert (
        mod.build_logql(threshold="info")
        == '{service=~".+",level=~"critical|error|info|warn"}'
    )
    assert (
        mod.build_logql(threshold="error")
        == '{service=~".+",level=~"critical|error"}'
    )
    assert mod.build_logql(level="error") == '{service=~".+",level="error"}'
    assert mod.build_logql(service="traefik", threshold="warn") == (
        '{service="traefik",level=~"critical|error|warn"}'
    )


@pytest.mark.unit
def test_logs_level_env_default_warn(monkeypatch):
    mod = _load_loki_stream()
    monkeypatch.delenv("LOGS_LEVEL", raising=False)
    assert mod.logs_level_from_env() == "warn"
    assert mod.build_logql() == '{service=~".+",level=~"critical|error|warn"}'
    monkeypatch.setenv("LOGS_LEVEL", "debug")
    assert mod.logs_level_from_env() == "debug"
    assert mod.build_logql() == '{service=~".+"}'
    monkeypatch.setenv("LOGS_LEVEL", "nope")
    assert mod.logs_level_from_env() == "warn"


@pytest.mark.unit
def test_poll_entries_respects_logs_level(monkeypatch):
    mod = _load_loki_stream()
    state = mod.StreamState(cursor_ns=0)

    def fake_query_range(**_kwargs):
        return LOKI_FIXTURE

    monkeypatch.setattr(mod, "query_range", fake_query_range)
    monkeypatch.setenv("LOGS_LEVEL", "warn")
    got = mod.poll_entries(state)
    assert len(got) == 1
    assert got[0]["level"] == "warn"
    assert got[0]["msg"] == "second"


@pytest.mark.unit
def test_extract_entries_and_dedup():
    mod = _load_loki_stream()
    entries = mod.extract_entries(LOKI_FIXTURE)
    assert len(entries) == 2
    assert entries[0]["msg"] == "first"
    state = mod.StreamState(cursor_ns=0)
    for entry in entries:
        assert mod.dedup_key(entry) not in state.seen
        state.note(entry)
    assert len(state.seen) == 2
    for entry in entries:
        assert mod.dedup_key(entry) in state.seen


@pytest.mark.unit
def test_poll_entries_advances_cursor(monkeypatch):
    mod = _load_loki_stream()
    state = mod.StreamState(cursor_ns=0)

    def fake_query_range(**_kwargs):
        return LOKI_FIXTURE

    monkeypatch.setattr(mod, "query_range", fake_query_range)
    monkeypatch.setenv("LOGS_LEVEL", "debug")
    got = mod.poll_entries(state)
    assert len(got) == 2
    assert state.cursor_ns > 1700000000000000002
    again = mod.poll_entries(state)
    assert again == []


@pytest.mark.unit
def test_collect_sse_frames_emits_n_entries(monkeypatch):
    mod = _load_loki_stream()
    state = mod.StreamState(cursor_ns=0)

    def fake_query_range(**_kwargs):
        return LOKI_FIXTURE

    monkeypatch.setattr(mod, "query_range", fake_query_range)
    monkeypatch.setenv("LOGS_LEVEL", "debug")
    frames = mod.collect_sse_frames(state)
    assert len(frames) == 2
    text = b"".join(frames).decode("utf-8")
    assert text.count("data: ") == 2
    assert '"msg":"first"' in text
    assert state.cursor_ns > 1700000000000000002
    assert mod.collect_sse_frames(state) == []


@pytest.mark.unit
def test_stream_state_prunes_seen():
    mod = _load_loki_stream()
    state = mod.StreamState(cursor_ns=0)
    monkeypatch_cap = 3
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, "seen_capacity", lambda: monkeypatch_cap)
    try:
        for i in range(5):
            state.note(
                {
                    "ts": f"2024-01-01T00:00:00.{i:03d}Z",
                    "service": "svc",
                    "level": "info",
                    "msg": f"m{i}",
                    "_ts_ns": str(i),
                }
            )
        assert len(state.seen) <= monkeypatch_cap
    finally:
        monkeypatch.undo()


@pytest.mark.unit
def test_logs_stream_guard_limits_concurrency():
    mod_path = SITE_APP / "logs_stream_guard.py"
    spec = importlib.util.spec_from_file_location("logs_stream_guard_unit", mod_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.reset_for_tests()
    mod.try_acquire_stream()
    mod.try_acquire_stream()
    mod.try_acquire_stream()
    assert mod.try_acquire_stream() is False
    mod.release_stream()
    assert mod.try_acquire_stream() is True
    mod.reset_for_tests()


@pytest.mark.unit
def test_poll_entries_query_failure_returns_empty(monkeypatch):
    mod = _load_loki_stream()
    state = mod.StreamState(cursor_ns=0)

    def boom(**_kwargs):
        raise ValueError("loki query_range HTTP 400: no")

    monkeypatch.setattr(mod, "query_range", boom)
    assert mod.poll_entries(state) == []
    assert mod.collect_sse_frames(state) == []


@pytest.mark.unit
def test_sse_handler_uses_http11(monkeypatch):
    srv_mod, _, _ = _load_server(monkeypatch)
    assert srv_mod.Handler.protocol_version == "HTTP/1.1"
    srv_mod, _, _ = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv_mod.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        import urllib.error
        import urllib.request

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(f"http://{host}:{port}/logs/stream", timeout=2)
        assert exc.value.code == 303
    finally:
        server.shutdown()
        thread.join(timeout=2)


STATIC = Path(__file__).resolve().parents[2] / "app/sites/ops-panel/app/static"


@pytest.mark.unit
def test_logs_frontend_has_no_loki_url():
    panel_js = ""
    for path in (STATIC / "js/panel").glob("*.js"):
        panel_js += path.read_text(encoding="utf-8")
    assert "loki" not in panel_js.lower()
    assert "3100" not in panel_js


@pytest.mark.unit
def test_logs_stream_slot_backoff():
    server = (STATIC.parent / "server.py").read_text(encoding="utf-8")
    assert "Retry-After" in server
    assert "_client_gone" in server
