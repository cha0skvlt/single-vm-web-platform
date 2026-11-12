"""Traefik auth fail2ban filter matches Traefik accessLog JSON lines."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

FILTER_DIR = Path(__file__).resolve().parents[2] / "ansible/roles/fail2ban/templates"

# fail2ban <ADDR> → IPv4 (unit test mirror; jail uses real fail2ban filter on host).
_ADDR = r"(?:\d{1,3}\.){3}\d{1,3}"


def _render_filter() -> str:
    env = Environment(loader=FileSystemLoader(FILTER_DIR), autoescape=False)
    return env.get_template("traefik-auth.conf.j2").render()


def _failregex_patterns(filter_text: str) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    collecting = False
    for line in filter_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("failregex"):
            collecting = True
            raw = stripped.split("=", 1)[1].strip()
            patterns.append(re.compile(raw.replace("<ADDR>", f"(?P<addr>{_ADDR})")))
            continue
        if collecting:
            if not stripped or stripped.startswith("#") or stripped.startswith("["):
                break
            patterns.append(re.compile(stripped.replace("<ADDR>", f"(?P<addr>{_ADDR})")))
    if not patterns:
        raise AssertionError("failregex not found in filter template")
    return patterns


def _first_match(patterns: list[re.Pattern[str]], line: str) -> re.Match[str] | None:
    for pattern in patterns:
        match = pattern.search(line)
        if match:
            return match
    return None


@pytest.mark.unit
def test_traefik_auth_filter_does_not_match_docker_wrapper():
    """SEC-5: jail reads Traefik accessLog.filePath, not Docker json-file wrapper."""
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"log":"{\\"ClientHost\\":\\"203.0.113.50\\",\\"RequestMethod\\":\\"POST\\",'
        '\\"RequestPath\\":\\"/login\\",\\"DownstreamStatus\\":401,\\"Duration\\":1200000}\\n",'
        '"stream":"stdout","time":"2026-06-08T10:00:00.000000000Z"}'
    )
    assert _first_match(patterns, line) is None


# Production Traefik accessLog order: ClientHost → DownstreamStatus → … → RequestMethod → RequestPath.
_PROD_LIKE_401 = (
    '{"ClientAddr":"93.153.255.147:32872","ClientHost":"93.153.255.147","ClientPort":"32872",'
    '"DownstreamContentSize":2850,"DownstreamStatus":401,"Duration":2060233,'
    '"RequestHost":"status.example.com","RequestMethod":"POST","RequestPath":"/login",'
    '"RequestProtocol":"HTTP/2.0","time":"2026-06-09T08:21:04Z"}'
)


@pytest.mark.unit
def test_traefik_auth_filter_matches_production_traefik_field_order():
    patterns = _failregex_patterns(_render_filter())
    match = _first_match(patterns, _PROD_LIKE_401)
    assert match is not None
    assert match.group("addr") == "93.153.255.147"


@pytest.mark.unit
def test_traefik_auth_filter_matches_plain_json_401():
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"ClientHost":"198.51.100.9","RequestMethod":"POST","RequestPath":"/login",'
        '"DownstreamStatus":401,"Duration":500000}'
    )
    match = _first_match(patterns, line)
    assert match is not None
    assert match.group("addr") == "198.51.100.9"


@pytest.mark.unit
def test_traefik_auth_filter_ignores_success_redirect():
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"ClientHost":"198.51.100.9","RequestMethod":"POST","RequestPath":"/login",'
        '"DownstreamStatus":303,"Duration":500000}'
    )
    assert _first_match(patterns, line) is None


@pytest.mark.unit
def test_traefik_auth_filter_matches_429_rate_limit():
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"ClientHost":"198.51.100.9","RequestMethod":"POST","RequestPath":"/login",'
        '"DownstreamStatus":429,"Duration":500000}'
    )
    match = _first_match(patterns, line)
    assert match is not None
    assert match.group("addr") == "198.51.100.9"


@pytest.mark.unit
def test_traefik_auth_filter_ignores_get_login():
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"ClientHost":"198.51.100.9","RequestMethod":"GET","RequestPath":"/login",'
        '"DownstreamStatus":200,"Duration":500000}'
    )
    assert _first_match(patterns, line) is None
