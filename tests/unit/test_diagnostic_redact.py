"""LOG-3 redaction tests."""

from __future__ import annotations

import json

import pytest

from scripts.diagnostic_redact import (
    PEM_REDACTED,
    REDACTED,
    is_sensitive_key,
    redact_dict,
    redact_string,
)


@pytest.mark.unit
def test_fake_secret_removed_from_json():
    raw = '{"username":"admin","password":"SUPERSECRET123","token":"abc"}'
    out = redact_string(raw)
    assert "SUPERSECRET" not in out
    assert "abc" not in out
    assert REDACTED in out


@pytest.mark.unit
def test_pem_redacted():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
    assert PEM_REDACTED in redact_string(pem)


@pytest.mark.unit
def test_env_assignment_redacted():
    line = "export GRAFANA_ADMIN_PASSWORD=changeme"
    out = redact_string(line)
    assert "changeme" not in out
    assert REDACTED in out


@pytest.mark.unit
def test_is_sensitive_key():
    assert is_sensitive_key("API_KEY")
    assert not is_sensitive_key("hostname")


@pytest.mark.unit
def test_redact_dict_nested():
    data = {"ok": True, "secret": "x", "nested": {"password": "y"}}
    out = redact_dict(data)
    assert out["secret"] == REDACTED
    assert out["nested"]["password"] == REDACTED


@pytest.mark.unit
def test_redact_non_json_and_bad_json():
    assert REDACTED in redact_string("api_key: leaked")
    assert "{" in redact_string("{not json")


@pytest.mark.unit
def test_redact_string_empty():
    assert redact_string("") == ""


@pytest.mark.unit
def test_redact_bearer_and_session_cookie():
    raw = 'Cookie: prod_session=abc123; Authorization: Bearer eyJhbGciOiJIUzI1NiJ9'
    out = redact_string(raw)
    assert "abc123" not in out
    assert "eyJhbGci" not in out
    assert REDACTED in out


@pytest.mark.unit
def test_redact_dict_redacts_lists():
    data = {
        "middlewares": [
            {"headers": {"customRequestHeaders": {"X-WEBAUTH-USER": "admin"}}},
        ]
    }
    out = redact_dict(data)
    assert out["middlewares"][0]["headers"]["customRequestHeaders"]["X-WEBAUTH-USER"] == REDACTED


@pytest.mark.unit
def test_redact_json_list_line():
    out = redact_string('[{"X-WEBAUTH-USER":"admin"}]')
    assert "admin" not in out
    assert REDACTED in out


@pytest.mark.unit
def test_redact_json_scalar_fallback(monkeypatch):
    import scripts.diagnostic_redact as mod

    real_loads = json.loads

    def loads(s):
        if s.strip() == '{"x":1}':
            return 42
        return real_loads(s)

    monkeypatch.setattr(mod.json, "loads", loads)
    assert mod._redact_jsonish_line('{"x":1}') == '{"x":1}'


@pytest.mark.unit
def test_redact_json_non_dict_branch(monkeypatch):
    import scripts.diagnostic_redact as mod

    real_loads = json.loads

    def loads(s):
        if s.strip() == '{"x":1}':
            return ["not", "dict"]
        return real_loads(s)

    monkeypatch.setattr(mod.json, "loads", loads)
    assert mod._redact_jsonish_line('{"x":1}') == '["not","dict"]'
