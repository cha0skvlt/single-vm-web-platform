"""LOG-3 — fail-closed redaction for diagnostic bundles."""

from __future__ import annotations

import json
import re
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|session|"
    r"htpasswd|credential|private[_-]?key|bearer|webauth|auth[_-]?header)"
)
ENV_ASSIGN_RE = re.compile(
    r"(?i)^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----",
    re.DOTALL,
)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)
PROD_SESSION_COOKIE_RE = re.compile(r"(prod_session=)[^;\s\"']+", re.IGNORECASE)
REDACTED = "[REDACTED]"
PEM_REDACTED = "[REDACTED PEM]"


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY_RE.search(key))


def redact_string(text: str) -> str:
    if not text:
        return text
    out = PEM_RE.sub(PEM_REDACTED, text)
    out = BEARER_RE.sub("Bearer [REDACTED]", out)
    out = PROD_SESSION_COOKIE_RE.sub(r"\1[REDACTED]", out)
    lines: list[str] = []
    for line in out.splitlines():
        m = ENV_ASSIGN_RE.match(line)
        if m:
            prefix, key, _value = m.group(1), m.group(2), m.group(3)
            if is_sensitive_key(key):
                lines.append(f"{prefix}{key}={REDACTED}")
                continue
        lines.append(_redact_jsonish_line(line))
    return "\n".join(lines)


def _redact_jsonish_line(line: str) -> str:
    stripped = line.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return _redact_key_value_patterns(line)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return _redact_key_value_patterns(line)
    if isinstance(data, dict):
        return json.dumps(redact_dict(data), ensure_ascii=False, separators=(",", ":"))
    if isinstance(data, list):
        return json.dumps(redact_value(data), ensure_ascii=False, separators=(",", ":"))
    return _redact_key_value_patterns(line)


def _redact_key_value_patterns(line: str) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if is_sensitive_key(key):
            return f"{key}={REDACTED}"
        return m.group(0)

    return re.sub(
        r"(?i)([A-Za-z_][A-Za-z0-9_-]*)\s*[:=]\s*([^\s,;]+)",
        repl,
        line,
    )


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_string(value)
    return value


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(key):
            out[key] = REDACTED
        else:
            out[key] = redact_value(value)
    return out
