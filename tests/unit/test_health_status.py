"""HEALTH-1 — aggregated health checks (ops-panel)."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

SITE_APP = Path(__file__).resolve().parents[2] / "app/sites/ops-panel/app"


def _load_health(monkeypatch):
    monkeypatch.setenv("PROD_ADMIN_USER", "admin")
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("HEALTH_CHECK_GIT", "1")
    monkeypatch.setenv("PEER_PUBLIC_URL", "https://git.example.com/")

    sites_name = "sites_status_health"
    sites_path = SITE_APP / "sites_status.py"
    sites_spec = importlib.util.spec_from_file_location(sites_name, sites_path)
    sites_mod = importlib.util.module_from_spec(sites_spec)
    assert sites_spec.loader is not None
    sys.modules[sites_name] = sites_mod
    sites_spec.loader.exec_module(sites_mod)
    sys.modules["sites_status"] = sites_mod
    sites_mod.reset_cache_for_tests()

    limits_path = SITE_APP / "request_limits.py"
    limits_spec = importlib.util.spec_from_file_location("request_limits", limits_path)
    limits_mod = importlib.util.module_from_spec(limits_spec)
    assert limits_spec.loader is not None
    sys.modules["request_limits"] = limits_mod
    limits_spec.loader.exec_module(limits_mod)
    limits_mod.reset_for_tests()

    paths_name = "install_paths_health"
    paths_path = SITE_APP / "install_paths.py"
    paths_spec = importlib.util.spec_from_file_location(paths_name, paths_path)
    paths_mod = importlib.util.module_from_spec(paths_spec)
    assert paths_spec.loader is not None
    sys.modules["install_paths"] = paths_mod
    paths_spec.loader.exec_module(paths_mod)

    loki_path = SITE_APP / "loki_stream.py"
    loki_spec = importlib.util.spec_from_file_location("loki_stream", loki_path)
    loki_mod = importlib.util.module_from_spec(loki_spec)
    assert loki_spec.loader is not None
    sys.modules["loki_stream"] = loki_mod
    loki_spec.loader.exec_module(loki_mod)

    f2b_path = SITE_APP / "fail2ban_status.py"
    f2b_spec = importlib.util.spec_from_file_location("fail2ban_status", f2b_path)
    f2b_mod = importlib.util.module_from_spec(f2b_spec)
    assert f2b_spec.loader is not None
    sys.modules["fail2ban_status"] = f2b_mod
    f2b_spec.loader.exec_module(f2b_mod)

    name = "prod_health_status_unit"
    path = SITE_APP / "health_status.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    mod.reset_cache_for_tests()
    monkeypatch.setattr(mod, "collect_load", lambda: {})
    mod._real_check_git_http = mod.check_git_http
    monkeypatch.setattr(mod, "check_git_http", lambda: mod._check("git_http", True, "HTTP 302"))
    monkeypatch.setattr(mod, "_live_tls_problems", lambda: [])
    monkeypatch.setattr(
        mod,
        "fail2ban_payload",
        lambda: {
            "status": "ok",
            "active_bans_count": 0,
            "bans_15m": 0,
            "bans_1h": 0,
            "detail": "ok",
            "reachable": True,
        },
    )
    return mod, sites_mod


@pytest.mark.unit
def test_health_payload_ok_when_all_checks_pass(monkeypatch):
    mod, sites_mod = _load_health(monkeypatch)

    def prom_ok(**kwargs):
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom_ok)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", True, "ok"))
    payload = mod.health_payload()
    assert payload["overall"] == "ok"
    assert len(payload["checks"]) == 3
    assert all(c["ok"] for c in payload["checks"])
    assert payload["git"]["overall"] == "ok"
    assert "fail2ban" in payload


@pytest.mark.unit
def test_health_payload_cache(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    calls = {"n": 0}

    def prom(**kwargs):
        calls["n"] += 1
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", True, "ok"))
    monkeypatch.setattr(mod, "health_cache_ttl_seconds", lambda: 60)
    mod.health_payload()
    mod.health_payload()
    assert calls["n"] == 2


@pytest.mark.unit
def test_each_check_failure_sets_overall_down(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    def prom_ok(**kwargs):
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom_ok)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", True, "ok"))

    monkeypatch.setattr(
        mod,
        "check_prometheus_targets",
        lambda **kwargs: mod._check(
            "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets",
            False,
            "down: job/a",
        )
        if not kwargs.get("peer_only")
        else mod._check("peer_prometheus_targets", True, "ok"),
    )
    assert mod.health_payload(force_refresh=True)["overall"] == "down"

    monkeypatch.setattr(
        mod,
        "check_prometheus_targets",
        lambda **kwargs: mod._check(
            "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets",
            True,
            "ok",
        ),
    )
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", False, "not up: x.test"))
    assert mod.health_payload(force_refresh=True)["overall"] == "down"

    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", False, "expired"))
    assert mod.health_payload(force_refresh=True)["overall"] == "down"


@pytest.mark.unit
def test_tls_warn_does_not_set_overall_down(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    def prom_ok(**kwargs):
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom_ok)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(
        mod, "check_tls", lambda: mod._check("tls", False, "example.com: expires in 19d")
    )
    payload = mod.health_payload(force_refresh=True)
    assert payload["overall"] == "ok"
    assert payload["incidents"]
    assert all(row["severity"] == "warn" for row in payload["incidents"])


@pytest.mark.unit
def test_parse_openssl_enddate_space_padded_day(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    got = mod._parse_openssl_enddate("notAfter=Aug  1 12:00:00 2026 GMT")
    assert got is not None
    assert got.day == 1
    assert got.month == 8


@pytest.mark.unit
def test_tls_problem_dedupes_wildcard_and_apex(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    problems: list[str] = []
    mod._add_tls_problem(problems, "example.com: expires in 19d")
    mod._add_tls_problem(problems, "*.example.com: expires in 19d")
    assert problems == ["example.com: expires in 19d"]


@pytest.mark.unit
def test_cached_health_picks_up_login_failure(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    limits = __import__("sys").modules["request_limits"]
    limits.reset_for_tests()

    def prom_ok(**kwargs):
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom_ok)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", True, "ok"))
    monkeypatch.setattr(mod, "health_cache_ttl_seconds", lambda: 60)
    first = mod.health_payload(force_refresh=True)
    assert all(row["id"] != "login" for row in first["incidents"])
    limits.record_login_failure("8.8.8.8")
    second = mod.health_payload()
    assert any(row["id"] == "login" for row in second["incidents"])
    limits.reset_for_tests()


@pytest.mark.unit
def test_health_site_rows_skip_local_infra(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    rows = [
        {"domain": "prod.example.com", "status": "up"},
        {"domain": "grafana.local", "status": "down"},
        {"domain": "_example.local", "status": "down"},
    ]
    scoped = mod._health_site_rows(rows)
    assert len(scoped) == 1
    assert scoped[0]["domain"] == "prod.example.com"


@pytest.mark.unit
def test_check_sites_reuses_cached_list(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    monkeypatch.setattr(
        mod,
        "sites_list",
        lambda **_: [
            {"domain": "a.test", "url": "https://a.test", "status": "up", "checked_at": "t"},
            {"domain": "b.test", "url": "https://b.test", "status": "down", "checked_at": "t"},
        ],
    )
    result = mod.check_sites_up()
    assert result["name"] == "sites"
    assert result["ok"] is False
    assert "b.test" in result["detail"]


@pytest.mark.unit
def test_check_tls_warn_and_expired(monkeypatch, tmp_path):
    mod, _ = _load_health(monkeypatch)
    cert_dir = tmp_path / "live.test"
    cert_dir.mkdir()
    pem = cert_dir / "fullchain.pem"
    pem.write_text("dummy", encoding="utf-8")
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    warn = datetime.now(timezone.utc) + timedelta(days=5)
    calls = {"n": 0}

    def fake_not_after(path):
        calls["n"] += 1
        if path.parent.name == "live.test":
            return expired
        return warn

    monkeypatch.setattr(mod, "CERTS_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_cert_not_after", fake_not_after)
    monkeypatch.setattr(mod, "tls_warn_days", lambda: 14)
    monkeypatch.setattr(mod, "_live_tls_problems", lambda: [])
    bad = mod.check_tls()
    assert bad["ok"] is False
    assert "live.test" in bad["detail"]


@pytest.mark.unit
def test_prometheus_targets_check(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    class Resp:
        def read(self):
            return json.dumps(
                {
                    "status": "success",
                    "data": {
                        "activeTargets": [
                            {
                                "health": "up",
                                "discoveredLabels": {"job": "j", "__address__": "i"},
                            },
                        ]
                    },
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    ok = mod.check_prometheus_targets()
    assert ok["name"] == "prometheus_targets"
    assert ok["ok"] is True


@pytest.mark.unit
def test_prometheus_targets_exclude_peer_from_main_check(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    class Resp:
        def read(self):
            return json.dumps(
                {
                    "status": "success",
                    "data": {
                        "activeTargets": [
                            {
                                "health": "up",
                                "labels": {"job": "node", "__address__": "localhost:9100"},
                            },
                            {
                                "health": "down",
                                "labels": {
                                    "job": "peer-optional",
                                    "__address__": ":9168",
                                    "host": "peer",
                                },
                            },
                        ]
                    },
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    main = mod.check_prometheus_targets()
    git = mod.check_prometheus_targets(peer_only=True)
    assert main["ok"] is True
    assert git["ok"] is True
    assert "peer-optional" not in git["detail"]


@pytest.mark.unit
def test_peer_prometheus_targets_fail_when_peer_node_down(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    class Resp:
        def read(self):
            return json.dumps(
                {
                    "status": "success",
                    "data": {
                        "activeTargets": [
                            {
                                "health": "down",
                                "labels": {
                                    "job": "peer-node",
                                    "__address__": ":9100",
                                    "host": "peer",
                                },
                            },
                            {
                                "health": "down",
                                "labels": {
                                    "job": "peer-optional",
                                    "__address__": ":9168",
                                    "host": "peer",
                                },
                            },
                        ]
                    },
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    git = mod.check_prometheus_targets(peer_only=True)
    assert git["ok"] is False
    assert "peer-node" in git["detail"]
    assert "peer-optional" not in git["detail"]


@pytest.mark.unit
def test_health_payload_includes_git_section(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    def prom_ok(**kwargs):
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom_ok)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", True, "ok"))
    payload = mod.health_payload(force_refresh=True)
    assert payload["overall"] == "ok"
    assert payload["git"]["overall"] == "ok"
    assert payload["git"]["checks"][0]["name"] == "peer_prometheus_targets"
    assert any(c["name"] == "git_http" for c in payload["git"]["checks"])
    assert payload["incidents"] == []
    assert "incidents" in payload


class _GitHttpResp:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.unit
def test_peer_public_href_sanitizes_and_rejects(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    assert mod.peer_public_href("https://git.example.com/") == "https://git.example.com"
    assert mod.peer_public_href("https://git.example.com/status") == "https://git.example.com/status"
    assert mod.peer_public_href("javascript:alert(1)") == ""
    assert mod.peer_public_href("https://user:pass@git.example.com") == ""
    assert mod.peer_public_href("") == ""
    monkeypatch.delenv("PEER_PUBLIC_URL", raising=False)
    monkeypatch.delenv("GIT_PROBE_URL", raising=False)
    assert mod.peer_public_href() == ""


@pytest.mark.unit
def test_check_git_http_ok_on_redirect_and_ok(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _GitHttpResp(302))
    got = mod._real_check_git_http()
    assert got["ok"] is True
    assert got["detail"] == "HTTP 302"
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _GitHttpResp(200))
    got = mod._real_check_git_http()
    assert got["ok"] is True
    assert got["detail"] == "HTTP 200"


@pytest.mark.unit
def test_check_git_http_down_on_500(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    def boom(*_a, **_k):
        raise urllib.error.HTTPError(
            url="https://git.example.com/",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    got = mod._real_check_git_http()
    assert got["ok"] is False
    assert got["detail"] == "HTTP 500"


@pytest.mark.unit
def test_git_http_500_marks_git_down_not_prod(monkeypatch):
    mod, _ = _load_health(monkeypatch)

    def prom_ok(**kwargs):
        name = "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets"
        return mod._check(name, True, "ok")

    monkeypatch.setattr(mod, "check_prometheus_targets", prom_ok)
    monkeypatch.setattr(mod, "check_sites_up", lambda: mod._check("sites", True, "ok"))
    monkeypatch.setattr(mod, "check_tls", lambda: mod._check("tls", True, "ok"))
    monkeypatch.setattr(mod, "check_git_http", lambda: mod._check("git_http", False, "HTTP 500"))
    payload = mod.health_payload(force_refresh=True)
    assert payload["overall"] == "ok"
    assert payload["git"]["overall"] == "down"
    git_inc = [row for row in payload["incidents"] if row["id"] == "git"]
    assert git_inc
    assert git_inc[0]["severity"] == "error"
    assert git_inc[0]["title"] == "Git down"
    assert git_inc[0]["detail"] == "HTTP 500"


@pytest.mark.unit
def test_build_incidents_sticky_rows(monkeypatch):
    mod, _ = _load_health(monkeypatch)
    checks = [
        mod._check("sites", False, "not up: a.test"),
        mod._check("tls", False, "a.test: expires in 19d; b.test: expired"),
        mod._check("prometheus_targets", False, "down: node/x"),
    ]
    git_checks = [mod._check("peer_prometheus_targets", False, "down: peer-node")]
    login = {"count": 2, "ips": 1, "last_ip": "1.2.3.4"}
    load = {"cpu": 90.0, "memory": 96.0, "disk": 40.0}
    rows = mod.build_incidents(checks, git_checks, load=load, login=login)
    ids = [r["id"] for r in rows]
    assert "sites" in ids
    assert any(r["id"].startswith("tls:") and r["severity"] == "warn" for r in rows)
    assert any(r["id"].startswith("tls:") and r["severity"] == "error" for r in rows)
    assert "prometheus" in ids
    assert "git-prometheus" in ids
    assert "fail2ban" not in ids
    assert "login" in ids
    assert "load:cpu" in ids
    assert "load:memory" in ids
    assert "load:disk" not in ids
    errors_first = [r["severity"] for r in rows]
    assert errors_first.index("error") < errors_first.index("warn")


@pytest.mark.unit
def test_health_endpoint_overall_in_server_and_frontend():
    server = (SITE_APP / "server.py").read_text(encoding="utf-8")
    assert "health_payload" in server
    assert 'path == "/health"' in server
    status_js = (SITE_APP / "static/js/panel/status.js").read_text(encoding="utf-8")
    tiles_js = (SITE_APP / "static/js/panel/tiles.js").read_text(encoding="utf-8")
    favicon_js = (SITE_APP / "static/js/panel/favicon.js").read_text(encoding="utf-8")
    assert "overall" in status_js
    assert "application/json" in status_js
    assert "gitStatus" in status_js
    assert "overall" in tiles_js
    assert "beaconKindFromStatus" in favicon_js
    assert "status-beacon--alarm" in tiles_js
    assert "status-beacon--stale" in tiles_js
    f2b_js = (SITE_APP / "static/js/panel/fail2ban-table.js").read_text(encoding="utf-8")
    assert "fail2ban" in f2b_js
    assert "Banned now" in f2b_js
    assert "f2b-stats" in f2b_js
    assert "tile-table--f2b" not in f2b_js
    assert "mountFail2banTable" in tiles_js
    assert "mountIncidentsTable" in tiles_js
    incidents_js = (SITE_APP / "static/js/panel/incidents-table.js").read_text(encoding="utf-8")
    assert "No open incidents" in incidents_js
    tiles_json = (SITE_APP / "static/tiles.json").read_text(encoding="utf-8")
    assert '"id": "incidents"' in tiles_json
    assert '"col"' not in tiles_json
    assert '"w"' not in tiles_json
    assert tiles_json.index('"id": "sites"') < tiles_json.index('"id": "fail2ban"') < tiles_json.index(
        '"id": "incidents"'
    )
    assert "Live logs" not in tiles_json
    assert '"id": "logs"' not in tiles_json
    panel_js = SITE_APP / "static/js/panel"
    assert not (panel_js / "lock.js").exists()
    assert not (panel_js / "ctx.js").exists()
    assert not (panel_js / "persist.js").exists()
    assert not (panel_js / "grid.js").exists()
    assert not (panel_js / "tile-types.js").exists()
    tiles_js = (SITE_APP / "static/js/panel/tiles.js").read_text(encoding="utf-8")
    assert "fitSnapCell" not in tiles_js
    assert "resizeTile" not in tiles_js
    assert "openCtx" not in tiles_js
    panel_grid = (SITE_APP / "static/css/panel-grid.css").read_text(encoding="utf-8")
    assert "minmax(0, 1fr) minmax(0, 1fr) minmax(0, 2fr)" in panel_grid
    assert "height: 100%" in panel_grid
    assert "overflow: hidden" in panel_grid
    assert "board-wrap--fill" not in panel_grid
    assert "board-wrap--locked" not in panel_grid
    responsive = (SITE_APP / "static/css/responsive.css").read_text(encoding="utf-8")
    assert "flex-basis: 100%" in responsive
    assert "@media (max-width: 48rem)" in responsive
    assert "@media (max-width: 768px)" not in responsive
    assert "flex: 0 0 auto" in responsive
    assert "justify-content: flex-start" in responsive
    main_js = (SITE_APP / "static/js/panel/main.js").read_text(encoding="utf-8")
    assert "renderBoard()" in main_js
    assert "initResponsiveLayout" not in main_js
    assert "toggleBoardLock" not in main_js
    inc_css = (SITE_APP / "static/css/incidents-table.css").read_text(encoding="utf-8")
    assert "--signal" in inc_css
    index_html = (SITE_APP / "static/index.html").read_text(encoding="utf-8")
    assert "incidents-table.css" in index_html
    assert "board-lock" not in index_html
    assert "ctx-menu" not in index_html
    assert "header-rule" not in index_html
    assert index_html.find('class="header-actions"') < index_html.find('id="header-status"')
    assert index_html.find('id="header-status"') < index_html.find('class="header-tools"')
    assert index_html.find('class="header-tools"') < index_html.find('id="theme-toggle-btn"')
    assert index_html.find('id="theme-toggle-btn"') < index_html.find('class="header-actions-primary"')
    components_css = (SITE_APP / "static/css/components.css").read_text(encoding="utf-8")
    status_css = components_css.split(".header-status {", 1)[1].split("}", 1)[0]
    tools_css = components_css.split(".header-tools {", 1)[1].split("}", 1)[0]
    actions_css = components_css.split(".header-actions {", 1)[1].split("}", 1)[0]
    assert "border-right: var(--border-hairline)" in status_css
    assert "padding-right: var(--space-4)" in status_css
    assert "border-right: var(--border-hairline)" in tools_css
    assert "padding-right: var(--space-4)" in tools_css
    assert "gap: var(--space-4)" in actions_css
    assert "flex-shrink: 1" in actions_css
    sites_css = (SITE_APP / "static/css/sites-table.css").read_text(encoding="utf-8")
    assert "--signal" in sites_css
    assert "--text-muted" in sites_css


@pytest.mark.unit
def test_beacon_kind_never_ok_when_stale():
    favicon = (SITE_APP / "static/js/panel/favicon.js").read_text(encoding="utf-8")
    assert "stale" in favicon
    assert "overall === 'ok'" in favicon or "status.ok" in favicon
