"""FAIL2BAN tile — Loki-backed jail table and recent bans."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import pytest

SITE_APP = Path(__file__).resolve().parents[2] / "app/sites/ops-panel/app"
NOW_SEC = 1_700_000_000
NOW_NS = NOW_SEC * 1_000_000_000


def _load_fail2ban(monkeypatch):
    monkeypatch.setenv("PROD_ADMIN_USER", "admin")
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "secret")

    loki_name = "loki_stream_fail2ban_unit"
    loki_path = SITE_APP / "loki_stream.py"
    loki_spec = importlib.util.spec_from_file_location(loki_name, loki_path)
    loki_mod = importlib.util.module_from_spec(loki_spec)
    assert loki_spec.loader is not None
    sys.modules[loki_name] = loki_mod
    loki_spec.loader.exec_module(loki_mod)
    sys.modules["loki_stream"] = loki_mod

    name = "fail2ban_status_unit"
    path = SITE_APP / "fail2ban_status.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "time", lambda: NOW_SEC)
    return mod, loki_mod


def _ban_payload(lines: list[tuple[str, str]]) -> dict:
    return {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {"service": "fail2ban", "level": "info"},
                    "values": [[ts, line] for ts, line in lines],
                }
            ]
        },
    }


@pytest.mark.unit
def test_threat_level_thresholds(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    assert mod.threat_level(0) == "ok"
    assert mod.threat_level(1) == "ok"
    assert mod.threat_level(2) == "warn"
    assert mod.threat_level(9) == "warn"
    assert mod.threat_level(10) == "crit"


@pytest.mark.unit
def test_banned_now_net_unban_and_jails(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    payload = _ban_payload(
        [
            ("1000", "NOTICE  [traefik-auth] Ban 1.2.3.4"),
            ("2000", "NOTICE  [traefik-auth] Ban 5.6.7.8"),
            ("3000", "NOTICE  [traefik-auth] Unban 1.2.3.4"),
            ("4000", "NOTICE  [sshd] Ban 9.9.9.9"),
        ]
    )
    monkeypatch.setattr(mod, "query_range", lambda **_: payload)
    result = mod.fail2ban_payload()
    assert result["reachable"] is True
    assert result["banned_now"] == 2
    assert result["active_jails"] == 3
    assert "sshd" in result["jail_names"]
    assert "traefik-auth" in result["jail_names"]
    assert "traefik-scan" in result["jail_names"]


@pytest.mark.unit
def test_recent_bans_sorted_newest_first(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    payload = _ban_payload(
        [
            (str(NOW_NS - 600_000_000_000), "NOTICE  [sshd] Ban 1.1.1.1"),
            (str(NOW_NS - 120_000_000_000), "NOTICE  [traefik-auth] Ban 2.2.2.2"),
        ]
    )
    monkeypatch.setattr(mod, "query_range", lambda **_: payload)
    result = mod.fail2ban_payload()
    assert result["banned_now"] == 2
    assert len(result["recent"]) == 2
    assert result["recent"][0]["ip"] == "2.2.2.2"
    assert result["recent"][0]["jail"] == "traefik-auth"
    assert "ago" in result["recent"][0]


@pytest.mark.unit
def test_recent_bans_omit_expired_unbans(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    payload = _ban_payload(
        [
            (str(NOW_NS - 8 * 3_600_000_000_000), "NOTICE  [traefik-scan] Ban 52.200.76.145"),
            (str(NOW_NS - 7 * 3_600_000_000_000), "NOTICE  [traefik-scan] Unban 52.200.76.145"),
            (str(NOW_NS - 4 * 3_600_000_000_000), "NOTICE  [traefik-scan] Ban 192.253.248.169"),
            (str(NOW_NS - 3 * 3_600_000_000_000), "NOTICE  [traefik-scan] Unban 192.253.248.169"),
        ]
    )
    monkeypatch.setattr(mod, "query_range", lambda **_: payload)
    result = mod.fail2ban_payload()
    assert result["banned_now"] == 0
    assert result["recent"] == []


@pytest.mark.unit
def test_recent_bans_dedupe_repeat_bans_same_ip(monkeypatch):
    """Active list must match banned_now: one row per active (jail, ip), latest Ban only."""
    mod, _ = _load_fail2ban(monkeypatch)
    payload = _ban_payload(
        [
            (str(NOW_NS - 12 * 3_600_000_000_000), "NOTICE  [traefik-scan] Ban 45.148.10.62"),
            (str(NOW_NS - 8 * 3_600_000_000_000), "NOTICE  [traefik-scan] Ban 45.148.10.62"),
            (str(NOW_NS - 30 * 60_000_000_000), "NOTICE  [traefik-scan] Ban 81.171.74.60"),
            (str(NOW_NS - 25 * 60_000_000_000), "NOTICE  [traefik-scan] Ban 45.148.10.62"),
            (str(NOW_NS - 21 * 60_000_000_000), "NOTICE  [traefik-scan] Ban 91.148.245.81"),
        ]
    )
    monkeypatch.setattr(mod, "query_range", lambda **_: payload)
    result = mod.fail2ban_payload()
    assert result["banned_now"] == 3
    assert len(result["recent"]) == 3
    assert [row["ip"] for row in result["recent"]] == [
        "91.148.245.81",
        "45.148.10.62",
        "81.171.74.60",
    ]
    assert result["recent"][1]["ago"] == "25m ago"


@pytest.mark.unit
def test_fail2ban_payload_loki_unreachable(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)

    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(mod, "query_range", boom)
    result = mod.fail2ban_payload()
    assert result["reachable"] is False
    assert result["status"] == "unknown"
    assert result["jail_names"] == []


@pytest.mark.unit
def test_fail2ban_payload_ok_when_no_bans(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    monkeypatch.setattr(
        mod,
        "query_range",
        lambda **_: {"status": "success", "data": {"result": []}},
    )
    result = mod.fail2ban_payload()
    assert result["status"] == "ok"
    assert result["bans_1h"] == 0
    assert result["banned_now"] == 0
    assert result["active_jails"] == 3


@pytest.mark.unit
def test_fail2ban_payload_crit_on_burst(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    lines = [
        (str(NOW_NS - i * 30_000_000_000), f"NOTICE  [sshd] Ban 10.0.0.{i}")
        for i in range(12)
    ]
    monkeypatch.setattr(mod, "query_range", lambda **_: _ban_payload(lines))
    result = mod.fail2ban_payload()
    assert result["status"] == "crit"
    assert result["bans_1h"] == 12


@pytest.mark.unit
def test_health_payload_includes_fail2ban(monkeypatch):
    monkeypatch.setenv("PROD_ADMIN_USER", "admin")
    monkeypatch.setenv("PROD_ADMIN_PASSWORD", "secret")

    sites_name = "sites_status_f2b"
    sites_path = SITE_APP / "sites_status.py"
    sites_spec = importlib.util.spec_from_file_location(sites_name, sites_path)
    sites_mod = importlib.util.module_from_spec(sites_spec)
    assert sites_spec.loader is not None
    sys.modules[sites_name] = sites_mod
    sites_spec.loader.exec_module(sites_mod)
    sys.modules["sites_status"] = sites_mod

    loki_path = SITE_APP / "loki_stream.py"
    loki_spec = importlib.util.spec_from_file_location("loki_stream", loki_path)
    loki_mod = importlib.util.module_from_spec(loki_spec)
    assert loki_spec.loader is not None
    sys.modules["loki_stream"] = loki_mod
    loki_spec.loader.exec_module(loki_mod)

    f2b_name = "fail2ban_status_health"
    f2b_path = SITE_APP / "fail2ban_status.py"
    f2b_spec = importlib.util.spec_from_file_location(f2b_name, f2b_path)
    f2b_mod = importlib.util.module_from_spec(f2b_spec)
    assert f2b_spec.loader is not None
    sys.modules[f2b_name] = f2b_mod
    f2b_spec.loader.exec_module(f2b_mod)
    sys.modules["fail2ban_status"] = f2b_mod

    limits_path = SITE_APP / "request_limits.py"
    limits_spec = importlib.util.spec_from_file_location("request_limits_f2b", limits_path)
    limits_mod = importlib.util.module_from_spec(limits_spec)
    assert limits_spec.loader is not None
    sys.modules["request_limits"] = limits_mod
    limits_spec.loader.exec_module(limits_mod)

    paths_path = SITE_APP / "install_paths.py"
    paths_spec = importlib.util.spec_from_file_location("install_paths_f2b", paths_path)
    paths_mod = importlib.util.module_from_spec(paths_spec)
    assert paths_spec.loader is not None
    sys.modules["install_paths"] = paths_mod
    paths_spec.loader.exec_module(paths_mod)

    health_name = "health_status_f2b"
    health_path = SITE_APP / "health_status.py"
    health_spec = importlib.util.spec_from_file_location(health_name, health_path)
    health_mod = importlib.util.module_from_spec(health_spec)
    assert health_spec.loader is not None
    sys.modules[health_name] = health_mod
    health_spec.loader.exec_module(health_mod)
    health_mod.reset_cache_for_tests()

    stub = {
        "status": "ok",
        "banned_now": 0,
        "active_jails": 2,
        "bans_1h": 0,
        "detail": "ok",
        "reachable": True,
        "jail_names": ["sshd", "traefik-auth"],
        "recent": [],
    }
    monkeypatch.setattr(
        health_mod,
        "check_prometheus_targets",
        lambda **kwargs: health_mod._check(
            "peer_prometheus_targets" if kwargs.get("peer_only") else "prometheus_targets",
            True,
            "ok",
        ),
    )
    monkeypatch.setattr(health_mod, "check_sites_up", lambda: health_mod._check("sites", True, "ok"))
    monkeypatch.setattr(health_mod, "check_tls", lambda: health_mod._check("tls", True, "ok"))
    monkeypatch.setattr(
        health_mod, "check_git_http", lambda: health_mod._check("git_http", True, "HTTP 302")
    )
    monkeypatch.setattr(health_mod, "collect_load", lambda: {})
    monkeypatch.setattr(health_mod, "fail2ban_payload", lambda: stub)

    payload = health_mod.health_payload(force_refresh=True)
    assert payload["fail2ban"] == stub


@pytest.mark.unit
def test_fail2ban_payload_warn_level(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    lines = [
        (str(NOW_NS - i * 60_000_000_000), f"NOTICE  [sshd] Ban 10.0.0.{i}")
        for i in range(5)
    ]
    monkeypatch.setattr(mod, "query_range", lambda **_: _ban_payload(lines))
    result = mod.fail2ban_payload()
    assert result["status"] == "warn"
    assert result["bans_1h"] == 5


@pytest.mark.unit
def test_fail2ban_payload_check_disabled(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    monkeypatch.setenv("HEALTH_CHECK_FAIL2BAN", "false")
    result = mod.fail2ban_payload()
    assert result["status"] == "ok"
    assert result["detail"] == "check disabled"
    assert result["reachable"] is True


@pytest.mark.unit
def test_format_ago_units(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    now = 1_000_000_000_000_000_000
    assert mod._format_ago(now - 30_000_000_000, now) == "30s ago"
    assert mod._format_ago(now - 120_000_000_000, now) == "2m ago"
    assert mod._format_ago(now - 7_200_000_000_000, now) == "2h ago"


@pytest.mark.unit
def test_skips_unmatched_lines(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    payload = _ban_payload(
        [
            ("1", "fail2ban started"),
            ("2", "NOTICE  [sshd] Ban 9.9.9.9"),
        ]
    )
    monkeypatch.setattr(mod, "query_range", lambda **_: payload)
    result = mod.fail2ban_payload()
    assert result["banned_now"] == 1


@pytest.mark.unit
def test_loki_logql_uses_loki_safe_ban_filter(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    captured: list[str] = []

    def capture(**kwargs):
        captured.append(kwargs.get("logql", ""))
        return {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(mod, "query_range", capture)
    mod.fail2ban_payload()
    assert captured
    assert "\\]" not in captured[0]
    assert "(Ban|Unban)" in captured[0]


@pytest.mark.unit
def test_lookback_and_window_env(monkeypatch):
    mod, _ = _load_fail2ban(monkeypatch)
    monkeypatch.setenv("FAIL2BAN_LOOKBACK_HOURS", "not-int")
    monkeypatch.setenv("FAIL2BAN_THREAT_WINDOW_MIN", "0")
    assert mod.lookback_hours() == 168
    assert mod.threat_window_minutes() == 1
