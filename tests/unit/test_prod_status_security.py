"""AUD-1..5 — ops-panel security and correctness."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SITE_APP = Path(__file__).resolve().parents[2] / "app/sites/ops-panel/app"


def _load_module(name: str, path: Path, *, as_name: str | None = None):
    mod_name = as_name or name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_limits():
    mod = _load_module("request_limits", SITE_APP / "request_limits.py")
    mod.reset_for_tests()
    return mod


def _load_guard():
    mod = _load_module("diagnostic_guard", SITE_APP / "diagnostic_guard.py")
    mod.reset_for_tests()
    return mod


def _load_server(monkeypatch, *, session_secret: str = "test-only-session-secret-32chars-min"):
    monkeypatch.setenv("PROD_ADMIN_USER", "admin")
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "secret")
    if session_secret:
        monkeypatch.setenv("PROD_SESSION_SECRET", session_secret)
    else:
        monkeypatch.delenv("PROD_SESSION_SECRET", raising=False)
    monkeypatch.setenv("LOGIN_RATE_LIMIT", "3")
    monkeypatch.setenv("LOGIN_RATE_WINDOW", "300")
    monkeypatch.setenv("DIAGNOSTIC_COOLDOWN", "0")

    for mod_name in list(sys.modules):
        if mod_name.startswith(("prod_status_", "prod_sites_", "prod_loki_", "loki_stream", "sites_status", "health_status", "fail2ban_status", "auth", "structured_log", "request_limits", "diagnostic_guard", "install_paths")):
            sys.modules.pop(mod_name, None)

    _load_module("request_limits", SITE_APP / "request_limits.py", as_name="request_limits").reset_for_tests()
    _load_module("diagnostic_guard", SITE_APP / "diagnostic_guard.py", as_name="diagnostic_guard").reset_for_tests()
    _load_module("logs_stream_guard", SITE_APP / "logs_stream_guard.py", as_name="logs_stream_guard").reset_for_tests()
    _load_module("auth", SITE_APP / "auth.py", as_name="auth")
    _load_module("structured_log", SITE_APP / "structured_log.py", as_name="structured_log")
    _load_module("loki_stream", SITE_APP / "loki_stream.py", as_name="loki_stream")
    _load_module("sites_status", SITE_APP / "sites_status.py", as_name="sites_status")
    _load_module("install_paths", SITE_APP / "install_paths.py", as_name="install_paths")
    _load_module("fail2ban_status", SITE_APP / "fail2ban_status.py", as_name="fail2ban_status")
    _load_module("health_status", SITE_APP / "health_status.py", as_name="health_status")
    return _load_module("prod_status_server_sec", SITE_APP / "server.py", as_name="prod_status_server_sec")


def _make_handler(srv):
    handler = srv.Handler.__new__(srv.Handler)
    handler.client_address = ("127.0.0.1", 0)
    return handler


@pytest.mark.unit
def test_client_ip_uses_traefik_xff_hop(monkeypatch):
    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    handler.headers = MagicMock()
    handler.headers.get = lambda k, d="": {
        "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
        "X-Real-Ip": "",
    }.get(k, d)
    assert handler._client_ip() == "5.6.7.8"


@pytest.mark.unit
def test_client_ip_ignores_x_real_ip(monkeypatch):
    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    handler.headers = MagicMock()
    handler.headers.get = lambda k, d="": {
        "X-Real-Ip": "9.9.9.9",
        "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
    }.get(k, d)
    assert handler._client_ip() == "5.6.7.8"


@pytest.mark.unit
def test_login_post_spoofed_xff_still_rate_limited(monkeypatch):
    srv = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    server.timeout = 2
    import http.client

    host, port = server.server_address
    body = "username=admin&password=wrong"
    base = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)),
        "X-Real-Ip": "real-client",
    }

    for i in range(3):
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        headers = {**base, "X-Forwarded-For": f"spoof-{i}, real-client"}
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/login", body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        thread.join(timeout=5)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(
        "POST",
        "/login",
        body=body,
        headers={**base, "X-Forwarded-For": "spoof-new, real-client"},
    )
    resp = conn.getresponse()
    assert resp.status == 429
    resp.read()
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_login_post_without_session_secret_returns_503(monkeypatch):
    srv = _load_server(monkeypatch, session_secret="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    import http.client

    host, port = server.server_address
    body = "username=admin&password=secret"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=body, headers=headers)
    resp = conn.getresponse()
    assert resp.status == 503
    resp.read()
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_verify_login_non_ascii_password_does_not_raise(monkeypatch):
    monkeypatch.setenv("PROD_ADMIN_USER", "admin")
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "пароль")
    monkeypatch.setenv("PROD_SESSION_SECRET", "test-only-session-secret-32chars-min")
    auth = _load_module("auth_nonascii", SITE_APP / "auth.py", as_name="auth_nonascii")
    assert auth.verify_login("admin", "wrong") is False
    assert auth.verify_login("admin", "пароль") is True


@pytest.mark.unit
def test_login_post_non_ascii_password_returns_401(monkeypatch):
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "пароль")
    srv = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    import http.client
    from urllib.parse import urlencode

    host, port = server.server_address
    body = urlencode({"username": "admin", "password": "другой"})
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=body, headers=headers)
    resp = conn.getresponse()
    assert resp.status == 401
    resp.read()
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_login_rate_limit_blocks_after_failures():
    mod = _load_limits()
    mod.reset_for_tests()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LOGIN_RATE_LIMIT", "2")
    try:
        assert mod.login_blocked("1.2.3.4") is False
        mod.record_login_failure("1.2.3.4")
        assert mod.login_blocked("1.2.3.4") is False
        mod.record_login_failure("1.2.3.4")
        assert mod.login_blocked("1.2.3.4") is True
        mod.clear_login_failures("1.2.3.4")
        assert mod.login_blocked("1.2.3.4") is False
        summary = mod.login_failure_summary()
        assert summary["count"] == 2
        assert summary["last_ip"] == "1.2.3.4"
    finally:
        monkeypatch.undo()
        mod.reset_for_tests()


@pytest.mark.unit
def test_diagnostic_guard_single_flight(monkeypatch):
    monkeypatch.setenv("DIAGNOSTIC_COOLDOWN", "0")
    mod = _load_guard()
    mod.reset_for_tests()
    ok1, _ = mod.try_begin()
    ok2, _ = mod.try_begin()
    assert ok1 is True
    assert ok2 is False
    mod.finish()
    ok3, _ = mod.try_begin()
    assert ok3 is True
    mod.finish()


@pytest.mark.unit
def test_diagnostic_guard_cooldown(monkeypatch):
    mod = _load_guard()
    mod.reset_for_tests()
    monkeypatch.setenv("DIAGNOSTIC_COOLDOWN", "60")
    ok1, _ = mod.try_begin()
    assert ok1 is True
    mod.finish()
    ok2, retry = mod.try_begin()
    assert ok2 is False
    assert retry is not None and retry > 0


@pytest.mark.unit
def test_resolve_static_rejects_escape(monkeypatch):
    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    assert handler._resolve_static("../server.py") is None
    assert handler._resolve_static("css/tokens.css") is not None


@pytest.mark.unit
def test_parse_form_rejects_oversized_body(monkeypatch):
    from email.message import Message

    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    headers = Message()
    headers["Content-Length"] = str(srv.MAX_FORM_BODY_BYTES + 1)
    handler.headers = headers
    handler.rfile = MagicMock()
    with pytest.raises(srv.BadRequestError):
        handler._parse_form()


@pytest.mark.unit
def test_parse_form_rejects_bad_content_length(monkeypatch):
    from email.message import Message

    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    headers = Message()
    headers["Content-Length"] = "nope"
    handler.headers = headers
    handler.rfile = MagicMock()
    with pytest.raises(srv.BadRequestError):
        handler._parse_form()


@pytest.mark.unit
def test_login_post_invalid_credentials_returns_401(monkeypatch):
    srv = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    import http.client

    host, port = server.server_address
    body = "username=admin&password=wrong"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=body, headers=headers)
    resp = conn.getresponse()
    assert resp.status == 401
    assert "max-age=31536000" in (resp.getheader("Strict-Transport-Security") or "")
    page = resp.read().decode("utf-8")
    assert 'id="login-error" class="login-error"' in page
    assert " hidden" not in page.split("login-error")[1][:80]
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_login_post_rate_limited(monkeypatch):
    srv = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    server.timeout = 2
    thread = threading.Thread(target=server.handle_request, daemon=True)
    import http.client

    host, port = server.server_address
    body = "username=admin&password=wrong"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}

    for _ in range(3):
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/login", body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        thread.join(timeout=5)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=body, headers=headers)
    resp = conn.getresponse()
    assert resp.status == 429
    resp.read()
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_unauth_redirect_sets_content_length(monkeypatch):
    srv = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    import http.client

    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=2)
    conn.request("GET", "/diagnostic")
    resp = conn.getresponse()
    assert resp.status == 303
    assert resp.getheader("Content-Length") == "0"
    assert resp.read() == b""
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_diagnostic_get_authed_returns_405(monkeypatch):
    srv = _load_server(monkeypatch)
    auth = sys.modules["auth"]
    cookie = auth.issue_session_cookie("admin")
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    import http.client

    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", "/diagnostic", headers={"Cookie": cookie.split(";")[0]})
    resp = conn.getresponse()
    assert resp.status == 405
    resp.read()
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_emit_log_thread_safe(monkeypatch):
    st = _load_module("structured_log_ts", SITE_APP / "structured_log.py", as_name="structured_log_ts")
    lines: list[str] = []
    lock = threading.Lock()

    def capture(data):
        with lock:
            lines.append(data)

    with patch.object(sys.stdout, "write", capture):
        with patch.object(sys.stdout, "flush", lambda: None):

            def worker(n: int):
                st.emit_log("info", "test.event", f"msg-{n}", {"n": n})

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

    assert len(lines) == 20
    for line in lines:
        payload = json.loads(line.strip())
        assert payload["event"] == "test.event"


@pytest.mark.unit
def test_health_unauth_loopback_returns_ok(monkeypatch):
    srv = _load_server(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    import http.client

    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.read() == b"ok\n"
    conn.close()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.unit
def test_health_unauth_non_loopback_redirects(monkeypatch):
    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    handler.client_address = ("172.28.10.6", 12345)
    handler.headers = MagicMock()
    handler.headers.get = lambda k, d="": d
    handler._current_user = lambda: None
    handler._redirect = MagicMock()
    handler._path = lambda: "/health"
    handler.do_GET()
    handler._redirect.assert_called_once_with("/login")


@pytest.mark.unit
def test_handler_hides_python_server_banner():
    srv = _load_module("server_banner", SITE_APP / "server.py", as_name="server_banner_unit")
    assert srv.Handler.server_version == "prod"
    assert srv.Handler.sys_version == ""
    handler = srv.Handler.__new__(srv.Handler)
    assert handler.version_string() == "prod"
    assert "Python" not in handler.version_string()
    assert "BaseHTTP" not in handler.version_string()
    assert "default-src 'self'" in srv.CONTENT_SECURITY_POLICY
    assert "fonts.googleapis.com" in srv.CONTENT_SECURITY_POLICY
    assert "max-age=31536000" in srv.STRICT_TRANSPORT_SECURITY


@pytest.mark.unit
def test_index_html_shows_peer_chip_when_url_set(monkeypatch):
    monkeypatch.setenv("PEER_PUBLIC_URL", "https://git.example.com/")
    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    sent = {}

    def capture(data, *, content_type, status=200):
        sent["body"] = data
        sent["content_type"] = content_type
        sent["status"] = status

    handler._send_bytes = capture
    handler._send_html("index.html")
    body = sent["body"].decode("utf-8")
    assert 'id="status-git-chip" href="https://git.example.com"' in body
    assert 'id="status-git-chip" hidden href="#"' not in body


@pytest.mark.unit
def test_index_html_keeps_peer_chip_hidden_without_url(monkeypatch):
    monkeypatch.delenv("PEER_PUBLIC_URL", raising=False)
    monkeypatch.delenv("GIT_PROBE_URL", raising=False)
    srv = _load_server(monkeypatch)
    handler = _make_handler(srv)
    sent = {}

    def capture(data, *, content_type, status=200):
        sent["body"] = data

    handler._send_bytes = capture
    handler._send_html("index.html")
    body = sent["body"].decode("utf-8")
    assert 'id="status-git-chip" hidden href="#"' in body
