"""Traefik scan fail2ban filter matches probe paths in Traefik accessLog JSON."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

FILTER_DIR = Path(__file__).resolve().parents[2] / "ansible/roles/fail2ban/templates"
_ADDR = r"(?:\d{1,3}\.){3}\d{1,3}"


def _render_filter() -> str:
    env = Environment(loader=FileSystemLoader(FILTER_DIR), autoescape=False)
    return env.get_template("traefik-scan.conf.j2").render()


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


_PROD_LIKE_ENV = (
    '{"ClientAddr":"185.177.72.205:51234","ClientHost":"185.177.72.205",'
    '"DownstreamStatus":404,"RequestMethod":"GET","RequestPath":"/.env",'
    '"time":"2026-06-16T10:00:00Z"}'
)

_PROD_LIKE_WP = (
    '{"ClientAddr":"5.61.209.43:44321","ClientHost":"5.61.209.43",'
    '"DownstreamStatus":404,"RequestMethod":"GET","RequestPath":"/wp-login.php",'
    '"time":"2026-06-16T10:01:00Z"}'
)


@pytest.mark.unit
def test_traefik_scan_filter_matches_env_probe():
    patterns = _failregex_patterns(_render_filter())
    match = _first_match(patterns, _PROD_LIKE_ENV)
    assert match is not None
    assert match.group("addr") == "185.177.72.205"


@pytest.mark.unit
def test_traefik_scan_filter_matches_wp_probe():
    patterns = _failregex_patterns(_render_filter())
    match = _first_match(patterns, _PROD_LIKE_WP)
    assert match is not None
    assert match.group("addr") == "5.61.209.43"


@pytest.mark.unit
def test_traefik_scan_filter_ignores_legitimate_pma_path():
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"ClientHost":"198.51.100.9","DownstreamStatus":200,'
        '"RequestMethod":"GET","RequestPath":"/pma/","time":"2026-06-16T10:00:00Z"}'
    )
    assert _first_match(patterns, line) is None


@pytest.mark.unit
def test_traefik_scan_filter_ignores_panel_login():
    patterns = _failregex_patterns(_render_filter())
    line = (
        '{"ClientHost":"198.51.100.9","DownstreamStatus":200,'
        '"RequestMethod":"GET","RequestPath":"/login","time":"2026-06-16T10:00:00Z"}'
    )
    assert _first_match(patterns, line) is None
