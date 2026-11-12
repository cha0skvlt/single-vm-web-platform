"""Cookie session auth for status.example.com panel."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

COOKIE_NAME = "prod_session"
SESSION_TTL_SEC = 60 * 60 * 12  # 12 h, renewed on login


def session_secret_configured() -> bool:
    return bool(os.environ.get("PROD_SESSION_SECRET", "").strip())


def _session_secret() -> bytes:
    raw = os.environ.get("PROD_SESSION_SECRET", "").strip()
    if not raw:
        raise RuntimeError("PROD_SESSION_SECRET is required")
    return raw.encode("utf-8")


def admin_credentials() -> tuple[str, str]:
    user = os.environ.get("PROD_ADMIN_USER", "").strip()
    password = os.environ.get("PROD_ADMIN_PASSWORD", "")
    return user, password


def credentials_configured() -> bool:
    user, password = admin_credentials()
    return bool(user and password and session_secret_configured())


def verify_login(username: str, password: str) -> bool:
    expected_user, expected_password = admin_credentials()
    if not expected_user or not expected_password:
        return False
    try:
        return secrets.compare_digest(
            username.encode("utf-8"), expected_user.encode("utf-8")
        ) and secrets.compare_digest(
            password.encode("utf-8"), expected_password.encode("utf-8")
        )
    except UnicodeEncodeError:
        return False


def issue_session_cookie(user: str) -> str:
    expiry = int(time.time()) + SESSION_TTL_SEC
    payload = f"{user}:{expiry}"
    sig = hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}:{sig}".encode("utf-8")).decode("ascii")
    return (
        f"{COOKIE_NAME}={token}; Path=/; HttpOnly; Secure; SameSite=Lax; "
        f"Max-Age={SESSION_TTL_SEC}"
    )


def clear_session_cookie() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"


def session_user(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    token = None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(f"{COOKIE_NAME}="):
            token = part.split("=", 1)[1]
            break
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if decoded.count(":") < 2:
        return None
    user, expiry_str, sig = decoded.rsplit(":", 2)
    payload = f"{user}:{expiry_str}"
    expected = hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    if expiry < int(time.time()):
        return None
    expected_user, _ = admin_credentials()
    if not expected_user:
        return None
    try:
        if not secrets.compare_digest(user.encode("utf-8"), expected_user.encode("utf-8")):
            return None
    except UnicodeEncodeError:
        return None
    return user
