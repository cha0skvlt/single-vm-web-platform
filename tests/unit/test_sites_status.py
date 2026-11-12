"""SITES-1 — Traefik domains and site probes (ops-panel)."""

from __future__ import annotations

import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

SITE_APP = Path(__file__).resolve().parents[2] / "app/sites/ops-panel/app"
STATIC = SITE_APP / "static"

ROUTERS_FIXTURE = [
    {"name": "a@docker", "rule": "Host(`alpha.test`)"},
    {"name": "b@docker", "rule": "Host(`beta.test`) && PathPrefix(`/app`)"},
    {"name": "c@docker", "rule": "PathPrefix(`/only`)"},
]


def _load_sites_status():
    path = SITE_APP / "sites_status.py"
    name = "prod_sites_status_unit"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    mod.reset_cache_for_tests()
    return mod


@pytest.fixture
def sites_mod():
    mod = _load_sites_status()
    mod.reset_cache_for_tests()
    yield mod
    mod.reset_cache_for_tests()


@pytest.mark.unit
def test_extract_host_domains():
    mod = _load_sites_status()
    assert mod.extract_host_domains("Host(`Example.COM`)") == ["example.com"]
    assert mod.extract_host_domains("Host(`a.test`) && Host(`b.test`)") == ["a.test", "b.test"]
    assert mod.extract_host_domains("PathPrefix(`/`)") == []


@pytest.mark.unit
def test_domains_from_routers(sites_mod):
    assert sites_mod.domains_from_routers(ROUTERS_FIXTURE) == ["alpha.test", "beta.test"]


@pytest.mark.unit
def test_classify_probe_status():
    mod = _load_sites_status()
    assert mod.classify_probe_status(200, False) == "up"
    assert mod.classify_probe_status(302, False) == "up"
    assert mod.classify_probe_status(503, False) == "down"
    assert mod.classify_probe_status(404, False) == "unknown"
    assert mod.classify_probe_status(None, True) == "down"


@pytest.mark.unit
def test_probe_domain_mapping(sites_mod, monkeypatch):
    class Resp:
        def __init__(self, status: int):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(req, timeout=0):
        host = req.headers.get("Host", "")
        if host == "up.test":
            return Resp(200)
        if host == "down.test":
            return Resp(503)
        if host == "unk.test":
            return Resp(418)
        raise OSError("fail")

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    assert sites_mod.probe_domain("up.test", probe_base="http://edge") == "up"
    assert sites_mod.probe_domain("down.test", probe_base="http://edge") == "down"
    assert sites_mod.probe_domain("unk.test", probe_base="http://edge") == "unknown"
    assert sites_mod.probe_domain("fail.test", probe_base="http://edge") == "down"


@pytest.mark.unit
def test_collect_sites_mock_traefik_and_probes(sites_mod, monkeypatch):
    monkeypatch.setattr(
        sites_mod,
        "fetch_traefik_routers",
        lambda *args, **kwargs: ROUTERS_FIXTURE,
    )

    def fake_probe(domain, *, probe_base=None, timeout=None):
        if domain == "alpha.test":
            return "up"
        if domain == "beta.test":
            return "down"
        return "unknown"

    monkeypatch.setattr(sites_mod, "probe_domain", fake_probe)
    rows = sites_mod.collect_sites()
    assert [r["domain"] for r in rows] == ["alpha.test", "beta.test"]
    by_domain = {r["domain"]: r for r in rows}
    assert by_domain["alpha.test"]["status"] == "up"
    assert by_domain["alpha.test"]["url"] == "https://alpha.test"
    assert by_domain["beta.test"]["status"] == "down"
    assert "https://" in by_domain["beta.test"]["url"]
    assert "://" not in by_domain["beta.test"]["domain"]


@pytest.mark.unit
def test_sites_list_single_flight_refresh(sites_mod, monkeypatch):
    import threading
    import time as time_mod

    monkeypatch.setattr(sites_mod, "status_ttl_seconds", lambda: 60)
    sites_mod._cache_rows = [sites_mod.build_site_row("stale.test", "down")]
    sites_mod._cache_at = time_mod.time() - 120
    calls = {"n": 0}

    def slow_collect(**_k):
        calls["n"] += 1
        time_mod.sleep(0.15)
        return [sites_mod.build_site_row("only.test", "up")]

    monkeypatch.setattr(sites_mod, "collect_sites", slow_collect)
    results: list[list[dict[str, str]]] = []

    def worker():
        results.append(sites_mod.sites_list())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert calls["n"] == 1
    assert len(results) == 3
    assert results[0] == results[1] == results[2]


@pytest.mark.unit
def test_sites_list_uses_cache(sites_mod, monkeypatch):
    calls = {"n": 0}

    def fake_collect(**_k):
        calls["n"] += 1
        return [sites_mod.build_site_row("only.test", "up")]

    monkeypatch.setattr(sites_mod, "collect_sites", fake_collect)
    monkeypatch.setattr(sites_mod, "status_ttl_seconds", lambda: 60)
    first = sites_mod.sites_list()
    second = sites_mod.sites_list()
    assert first == second
    assert calls["n"] == 1


@pytest.mark.unit
def test_sites_list_returns_stale_cache_on_traefik_error(sites_mod, monkeypatch):
    monkeypatch.setattr(
        sites_mod,
        "collect_sites",
        lambda **_k: (_ for _ in ()).throw(OSError("traefik down")),
    )
    monkeypatch.setattr(sites_mod, "status_ttl_seconds", lambda: 60)
    seeded = [sites_mod.build_site_row("cached.test", "up")]
    sites_mod._cache_rows = seeded
    sites_mod._cache_at = __import__("time").time()
    assert sites_mod.sites_list() == seeded


@pytest.mark.unit
def test_sites_py_has_no_hardcoded_domains():
    text = (SITE_APP / "sites_status.py").read_text(encoding="utf-8")
    assert "example.com" not in text
    assert "/sites" in (SITE_APP / "server.py").read_text(encoding="utf-8")


@pytest.mark.unit
def test_sites_frontend_blank_links_have_noopener():
    js = (STATIC / "js/panel/sites-table.js").read_text(encoding="utf-8")
    assert 'target="_blank"' in js
    assert "noopener noreferrer" in js
    assert "https://" not in js


@pytest.mark.unit
def test_sites_table_css_token_only():
    import re

    text = (STATIC / "css/sites-table.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text)
