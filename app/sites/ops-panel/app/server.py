#!/usr/bin/env python3
"""Ops panel — form login, health, and operator shortcuts."""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from html import escape as html_escape
from urllib.parse import parse_qs, urlparse

from loki_stream import (
    StreamState,
    collect_sse_frames,
    initial_cursor_ns,
    poll_interval_seconds,
)
from health_status import health_payload, invalidate_cache, peer_public_href
from sites_status import sites_list

import diagnostic_guard
import logs_stream_guard
import request_limits
from auth import (
    clear_session_cookie,
    credentials_configured,
    issue_session_cookie,
    session_user,
    verify_login,
)
from structured_log import emit_log
from install_paths import app_root, install_root, project_hostname

STATIC = Path(__file__).resolve().parent / "static"
STATIC_ROOT = STATIC.resolve()
PORT = int(os.environ.get("PORT", "8080"))
MAX_FORM_BODY_BYTES = 65536
DIAGNOSTIC_TIMEOUT = int(os.environ.get("DIAGNOSTIC_TIMEOUT", "120"))
DIAGNOSTIC_HOURS = os.environ.get("DIAGNOSTIC_HOURS", "2")
DIAGNOSTIC_MAX_MB = os.environ.get("DIAGNOSTIC_MAX_MB", "32")
DIAGNOSTIC_FULL = os.environ.get("DIAGNOSTIC_FULL", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Panel-only CSP (Traefik file provider watch:false — set here, not via @file middleware).
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
STRICT_TRANSPORT_SECURITY = "max-age=31536000; includeSubDomains; preload"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

PUBLIC_PATHS = frozenset(
    {
        "/login",
        "/css/tokens.css",
        "/css/login.css",
        "/img/mark.svg",
        "/js/login.js",
    }
)


class BadRequestError(Exception):
    """Malformed or oversized HTTP request body."""


class Handler(BaseHTTPRequestHandler):
    # Do not advertise BaseHTTP/Python version on the wire.
    server_version = "prod"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def version_string(self) -> str:
        return self.server_version

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("Strict-Transport-Security", STRICT_TRANSPORT_SECURITY)
        super().end_headers()

    def _trace_id(self) -> str | None:
        traceparent = self.headers.get("traceparent", "")
        parts = traceparent.split("-")
        if len(parts) >= 2 and len(parts[1]) >= 16:
            return parts[1][:32]
        return None

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover — silence stdlib access log
        return

    def _path(self) -> str:
        path = self.path.split("?", 1)[0]
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return path

    def _current_user(self) -> str | None:
        return session_user(self.headers.get("Cookie"))

    def _public_origin(self) -> str:
        proto = self.headers.get("X-Forwarded-Proto", "https").split(",", 1)[0].strip()
        host = (
            self.headers.get("X-Forwarded-Host")
            or self.headers.get("Host")
            or os.environ.get("PROD_PUBLIC_HOST", "status.example.com")
        )
        host = host.split(",", 1)[0].strip()
        return f"{proto}://{host}"

    def _redirect(self, location: str, *, cookies: list[str] | None = None) -> None:
        if location.startswith("/"):
            location = f"{self._public_origin()}{location}"
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        if cookies:
            for header in cookies:
                self.send_header("Set-Cookie", header)
        self.end_headers()

    def _require_user(self) -> str | None:
        user = self._current_user()
        if user:
            return user
        if self._path() in PUBLIC_PATHS:
            return None
        self._redirect("/login")
        return None

    def _is_loopback_client(self) -> bool:
        addr = self.client_address[0] if self.client_address else ""
        return addr in ("127.0.0.1", "::1")

    def _client_ip(self) -> str:
        if self.headers:
            xff = self.headers.get("X-Forwarded-For", "").strip()
            if xff:
                return xff.split(",")[-1].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def _resolve_static(self, rel: str) -> Path | None:
        try:
            candidate = (STATIC_ROOT / rel).resolve()
        except (OSError, ValueError, RuntimeError):
            return None
        try:
            candidate.relative_to(STATIC_ROOT)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def _parse_form(self) -> dict[str, str]:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise BadRequestError("invalid Content-Length") from exc
        if length < 0 or length > MAX_FORM_BODY_BYTES:
            raise BadRequestError("body too large")
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        parsed = parse_qs(body, keep_blank_values=True)
        return {key: values[0] if values else "" for key, values in parsed.items()}

    def _send_bytes(self, data: bytes, *, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, rel: str, *, status: int = 200) -> None:
        file_path = self._resolve_static(rel)
        if file_path is None:
            self.send_error(404)
            return
        body = file_path.read_bytes()
        if rel == "index.html":
            href = peer_public_href()
            if href:
                safe = html_escape(href, quote=True)
                body = body.replace(
                    b'id="status-git-chip" hidden href="#"',
                    f'id="status-git-chip" href="{safe}"'.encode("utf-8"),
                    1,
                )
        self._send_bytes(
            body,
            content_type=MIME.get(file_path.suffix, "application/octet-stream"),
            status=status,
        )

    def _send_login_page(self, *, show_error: bool = False, status: int = 200) -> None:
        file_path = self._resolve_static("login.html")
        if file_path is None:
            self.send_error(404)
            return
        body = file_path.read_bytes()
        if show_error:
            body = body.replace(
                b'id="login-error" class="login-error" hidden',
                b'id="login-error" class="login-error"',
            )
        self._send_bytes(body, content_type="text/html; charset=utf-8", status=status)

    def _diagnostic_filename(self) -> str:
        host = os.environ.get("PROD_PUBLIC_HOST", "status.example.com").split(".")[0]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"diagnostic-{host}-{stamp}.ndjson"

    def _logs_stream_filters(self) -> tuple[str | None, str | None]:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=False)
        level = (qs.get("level") or [None])[0]
        service = (qs.get("service") or [None])[0]
        level = level.strip() if isinstance(level, str) and level.strip() else None
        service = service.strip() if isinstance(service, str) and service.strip() else None
        return level, service

    def _client_gone(self) -> bool:
        sock = getattr(self, "connection", None)
        if sock is None:
            return True
        try:
            ready, _, _ = select.select([sock], [], [], 0)
            if not ready:
                return False
            data = sock.recv(1, socket.MSG_PEEK)
            return data == b""
        except OSError:
            return True

    def _serve_logs_stream(self) -> None:
        if not logs_stream_guard.try_acquire_stream():
            self.send_response(503)
            self.send_header("Retry-After", "15")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            body = b"Too many log streams\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            level, service = self._logs_stream_filters()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            state = StreamState(cursor_ns=initial_cursor_ns())
            interval = poll_interval_seconds()
            deadline = time.time() + logs_stream_guard.stream_max_duration_seconds()
            while time.time() < deadline:
                if self._client_gone():
                    return
                for frame in collect_sse_frames(
                    state, level=level, service=service, timeout=3
                ):
                    self.wfile.write(frame)
                    self.wfile.flush()
                self.wfile.write(b": \n\n")
                self.wfile.flush()
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            logs_stream_guard.release_stream()

    def _diagnostic_env(self) -> dict[str, str]:
        root = install_root()
        app = app_root()
        panel_app = app / "sites/ops-panel/app"
        return {
            **os.environ,
            "PYTHONPATH": f"{root}:{panel_app}",
            "PROJECT_HOSTNAME": project_hostname(),
            "DIAGNOSTIC_RUNTIME": "panel",
        }

    def _serve_diagnostic(self) -> None:
        allowed, retry_after = diagnostic_guard.try_begin()
        if not allowed:
            if retry_after is not None:
                self.send_response(429)
                self.send_header("Retry-After", str(retry_after))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_error(503, "Diagnostic already running")
            return

        out_path = Path(f"/tmp/{self._diagnostic_filename()}")
        sha_path = Path(f"{out_path}.sha256")
        cmd = [
            sys.executable,
            "-m",
            "scripts.diagnostic",
            "--hours",
            str(DIAGNOSTIC_HOURS),
            "--max-mb",
            str(DIAGNOSTIC_MAX_MB),
            "-o",
            str(out_path),
        ]
        if DIAGNOSTIC_FULL:
            cmd.append("--full")

        try:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd="/tmp",
                    env=self._diagnostic_env(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=DIAGNOSTIC_TIMEOUT,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                emit_log(
                    "warn",
                    "diagnostic.build_timeout",
                    "diagnostic bundle timed out",
                    {"timeout_sec": DIAGNOSTIC_TIMEOUT},
                    trace_id=self._trace_id(),
                )
                self.send_error(503, "Diagnostic timed out")
                return
            except OSError as exc:
                emit_log(
                    "warn",
                    "diagnostic.spawn_fail",
                    "diagnostic subprocess failed",
                    {"error": str(exc)},
                    trace_id=self._trace_id(),
                )
                self.send_error(503)
                return

            if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
                detail = (proc.stderr or "").strip()[:500]
                emit_log(
                    "warn",
                    "diagnostic.bundle_fail",
                    "diagnostic bundle failed",
                    {"returncode": proc.returncode, "detail": detail},
                    trace_id=self._trace_id(),
                )
                self.send_error(503)
                return

            size = out_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{self._diagnostic_filename()}"',
            )
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            with out_path.open("rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            out_path.unlink(missing_ok=True)
            sha_path.unlink(missing_ok=True)
            diagnostic_guard.finish()

    def _send_static(self, rel: str) -> None:
        file_path = self._resolve_static(rel)
        if file_path is None:
            self.send_error(404)
            return
        self._send_bytes(
            file_path.read_bytes(),
            content_type=MIME.get(file_path.suffix, "application/octet-stream"),
        )

    def do_GET(self) -> None:
        path = self._path()
        if path == "/health":
            if self._current_user():
                payload = health_payload()
                body = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self._is_loopback_client():
                body = b"ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._redirect("/login")
            return
        if path == "/auth/verify":
            if self._current_user():
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._redirect("/login")
            return
        if path == "/login":
            if self._current_user():
                self._redirect("/")
                return
            self._send_html("login.html")
            return
        if path in PUBLIC_PATHS:
            rel = path.lstrip("/")
            self._send_static(rel)
            return
        if path in ("", "/"):
            if not self._require_user():
                return
            self._send_html("index.html")
            return
        if path == "/diagnostic":
            if not self._require_user():
                return
            self.send_error(405, "Use POST for diagnostic download")
            return
        if path == "/status":
            if not self._require_user():
                return
            body = json.dumps(health_payload(), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/logs/stream":
            if not self._require_user():
                return
            self._serve_logs_stream()
            return
        if path == "/sites":
            if not self._require_user():
                return
            body = json.dumps(sites_list(), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._require_user():
            return
        rel = path.lstrip("/")
        self._send_static(rel)

    def do_POST(self) -> None:
        path = self._path()
        if path == "/login":
            if not credentials_configured():
                self.send_error(503)
                return
            client_ip = self._client_ip()
            if request_limits.login_blocked(client_ip):
                self.send_error(429)
                return
            try:
                form = self._parse_form()
            except BadRequestError:
                self.send_error(400)
                return
            username = form.get("username", "")
            password = form.get("password", "")
            if not verify_login(username, password):
                request_limits.record_login_failure(client_ip)
                invalidate_cache()
                emit_log(
                    "warn",
                    "auth.login_fail",
                    "invalid credentials",
                    {"remote": client_ip},
                )
                self._send_login_page(show_error=True, status=401)
                return
            request_limits.clear_login_failures(client_ip)
            emit_log(
                "info",
                "auth.login_ok",
                "session issued",
                {"username": username},
            )
            self._redirect("/", cookies=[issue_session_cookie(username)])
            return
        if path == "/diagnostic":
            if not self._require_user():
                return
            self._serve_diagnostic()
            return
        if path == "/logout":
            self._redirect("/login", cookies=[clear_session_cookie()])
            return
        self.send_error(405)


def main() -> None:
    emit_log("info", "server.start", "listening", {"port": PORT, "bind": "0.0.0.0"})
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
