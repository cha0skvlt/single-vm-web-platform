"""HEALTH-1 — aggregated infra health for panel lamp and favicon."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import request_limits
from fail2ban_status import fail2ban_payload
from install_paths import app_root
from sites_status import sites_list

PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
CERTS_ROOT = app_root() / "certs"

_health_lock = threading.Lock()
_refresh_lock = threading.Lock()
_health_cache: dict[str, Any] | None = None
_health_cache_at: float = 0.0


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def tls_warn_days() -> int:
    return _env_int("TLS_WARN_DAYS", 30)


def load_warn_pct() -> int:
    return max(1, min(_env_int("LOAD_WARN_PCT", 85), 99))


def load_error_pct() -> int:
    return max(load_warn_pct(), min(_env_int("LOAD_ERROR_PCT", 95), 100))


def health_cache_ttl_seconds() -> int:
    return max(1, _env_int("HEALTH_CACHE_TTL", 15))


def invalidate_cache() -> None:
    global _health_cache, _health_cache_at
    with _health_lock:
        _health_cache = None
        _health_cache_at = 0.0


def reset_cache_for_tests() -> None:
    invalidate_cache()


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _target_labels(target: dict[str, Any]) -> dict[str, Any]:
    labels = target.get("labels") or target.get("discoveredLabels") or {}
    return labels if isinstance(labels, dict) else {}


def _is_peer_target(target: dict[str, Any]) -> bool:
    labels = _target_labels(target)
    job = labels.get("job", "")
    if isinstance(job, str) and job.startswith("peer"):
        return True
    return labels.get("host") == "peer"


def _is_optional_peer_exporter(target: dict[str, Any]) -> bool:
    """Optional peer exporter (for example a GitLab exporter that may be off)."""
    return _target_labels(target).get("job") == "peer-optional"


def git_probe_url() -> str:
    return (
        os.environ.get("PEER_PUBLIC_URL")
        or os.environ.get("GIT_PROBE_URL")
        or ""
    ).strip()


def peer_public_href(raw: str | None = None) -> str:
    """HTTPS/HTTP origin+path for the peer chip. Empty if unset or unsafe."""
    value = (raw if raw is not None else git_probe_url()).strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    if "@" in parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def git_probe_timeout_seconds() -> float:
    return float(_env_int("GIT_PROBE_TIMEOUT", 8))


def check_git_http() -> dict[str, Any]:
    url = git_probe_url()
    if not url:
        return _check("git_http", True, "peer URL not configured")
    timeout = git_probe_timeout_seconds()
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _check("git_http", False, f"unreachable: {exc}")
    if 200 <= status < 400:
        return _check("git_http", True, f"HTTP {status}")
    return _check("git_http", False, f"HTTP {status}")


def check_prometheus_targets(*, peer_only: bool = False) -> dict[str, Any]:
    # Use active scrape targets — TSDB `up` keeps stale series after jobs are removed.
    check_name = "peer_prometheus_targets" if peer_only else "prometheus_targets"
    url = f"{PROMETHEUS}/api/v1/targets"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return _check(check_name, False, f"prometheus unreachable: {exc}")
    if payload.get("status") != "success":
        return _check(check_name, False, "prometheus bad response")
    down: list[str] = []
    for target in payload.get("data", {}).get("activeTargets", []):
        is_peer = _is_peer_target(target)
        if peer_only and not is_peer:
            continue
        if not peer_only and is_peer:
            continue
        if target.get("health") == "up":
            continue
        labels = _target_labels(target)
        if peer_only and _is_optional_peer_exporter(target):
            continue
        job = labels.get("job", "")
        instance = labels.get("__address__") or labels.get("instance", "")
        label = f"{job}/{instance}" if job and instance else instance or job or "unknown"
        down.append(label)
    if down:
        return _check(check_name, False, "down: " + ", ".join(sorted(down)))
    scope = "peer targets" if peer_only else "all targets"
    return _check(check_name, True, f"{scope} up")


def _health_site_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Public site routers only — skip .local dashboards and _example templates."""
    out: list[dict[str, str]] = []
    for row in rows:
        domain = (row.get("domain") or "").strip().lower()
        if not domain or domain.endswith(".local") or domain.startswith("_"):
            continue
        out.append(row)
    return out


def check_sites_up() -> dict[str, Any]:
    rows = sites_list()
    scoped = _health_site_rows(rows)
    if not scoped:
        if not rows:
            return _check("sites", True, "no routed domains")
        return _check("sites", True, "infra hosts only (.local / _)")
    bad = [r["domain"] for r in scoped if r.get("status") != "up"]
    if bad:
        return _check("sites", False, "not up: " + ", ".join(sorted(bad)))
    return _check("sites", True, f"{len(scoped)} site(s) up")


def _parse_openssl_enddate(text: str) -> datetime | None:
    line = text.strip()
    if line.startswith("notAfter="):
        line = line.split("=", 1)[1].strip()
    line = re.sub(r"\s+", " ", line)
    try:
        return datetime.strptime(line, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cert_not_after(pem: Path) -> datetime | None:
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-in", str(pem), "-noout", "-enddate"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_openssl_enddate(proc.stdout or proc.stderr or "")


def _openssl_available() -> bool:
    try:
        proc = subprocess.run(
            ["openssl", "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_tls() -> dict[str, Any]:
    if not _openssl_available():
        return _check("tls", True, "skipped (openssl not installed)")
    warn = tls_warn_days()
    now = datetime.now(timezone.utc)
    problems: list[str] = []
    if CERTS_ROOT.is_dir():
        for pem in sorted(CERTS_ROOT.glob("*/fullchain.pem")):
            domain = pem.parent.name
            if domain.startswith("_"):
                continue
            expiry = _cert_not_after(pem)
            if expiry is None:
                _add_tls_problem(problems, f"{domain}: unreadable cert")
                continue
            days_left = (expiry - now).total_seconds() / 86400
            if days_left < 0:
                _add_tls_problem(problems, f"{domain}: expired")
            elif days_left <= warn:
                _add_tls_problem(problems, f"{domain}: expires in {int(days_left)}d")
    for item in _live_tls_problems():
        _add_tls_problem(problems, item)
    if problems:
        return _check("tls", False, "; ".join(problems))
    return _check("tls", True, "certificates within warn window")


_CN_RE = re.compile(r"CN\s*=\s*([^,\n/]+)", re.IGNORECASE)

_CPU_QUERY = (
    '100 * (1 - avg(rate(node_cpu_seconds_total{job="node",mode="idle"}[5m])))'
)
_MEM_QUERY = (
    '100 * (1 - node_memory_MemAvailable_bytes{job="node"}'
    " / node_memory_MemTotal_bytes{job=\"node\"})"
)
_DISK_QUERY = (
    '100 - (node_filesystem_avail_bytes{job="node",mountpoint="/",'
    'fstype!~"tmpfs|overlay|squashfs|aufs"}'
    " / node_filesystem_size_bytes{job=\"node\",mountpoint=\"/\","
    'fstype!~"tmpfs|overlay|squashfs|aufs"} * 100)'
)


def _tls_problem_key(line: str) -> tuple[str, str]:
    host, _, rest = line.partition(":")
    host = host.strip().lower()
    if host.startswith("*."):
        host = host[2:]
    return host, rest.strip()


def _add_tls_problem(problems: list[str], line: str) -> None:
    key = _tls_problem_key(line)
    for existing in problems:
        if _tls_problem_key(existing) == key:
            return
    problems.append(line)


def _tls_dial() -> str:
    parsed = urllib.parse.urlparse(os.environ.get("TRAEFIK_PROBE_URL", "http://traefik:80"))
    host = parsed.hostname or "traefik"
    return f"{host}:443"


def _parse_cn(subject: str) -> str:
    match = _CN_RE.search(subject or "")
    if match:
        return match.group(1).strip()
    return ""


def _probe_live_cert(domain: str) -> tuple[str, datetime] | None:
    dial = _tls_dial()
    try:
        client = subprocess.run(
            ["openssl", "s_client", "-connect", dial, "-servername", domain],
            input="",
            capture_output=True,
            text=True,
            errors="replace",
            timeout=3,
            check=False,
        )
        parsed = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate", "-subject"],
            input=client.stdout or "",
            capture_output=True,
            text=True,
            errors="replace",
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError, UnicodeError):
        return None
    if parsed.returncode != 0:
        return None
    expiry = None
    cn = ""
    for line in (parsed.stdout or "").splitlines():
        if expiry is None:
            expiry = _parse_openssl_enddate(line)
        if not cn:
            cn = _parse_cn(line)
    if expiry is None:
        return None
    return cn or domain, expiry


def _live_tls_problems() -> list[str]:
    rows = _health_site_rows(sites_list())
    if not rows:
        return []
    warn = tls_warn_days()
    now = datetime.now(timezone.utc)
    seen: dict[str, str] = {}
    to_probe = [
        r["domain"]
        for r in rows
        if not (CERTS_ROOT / r["domain"] / "fullchain.pem").is_file()
    ]
    if not to_probe:
        return []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(to_probe)))) as pool:
        futures = [pool.submit(_probe_live_cert, domain) for domain in to_probe]
        for fut in as_completed(futures):
            try:
                got = fut.result()
            except Exception:
                continue
            if not got:
                continue
            cn, expiry = got
            days_left = (expiry - now).total_seconds() / 86400
            if days_left < 0:
                line = f"{cn}: expired"
            elif days_left <= warn:
                line = f"{cn}: expires in {int(days_left)}d"
            else:
                continue
            seen[cn] = line
    return [seen[k] for k in sorted(seen)]


def _prometheus_scalar(query: str) -> float | None:
    params = urllib.parse.urlencode({"query": query})
    url = f"{PROMETHEUS}/api/v1/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if payload.get("status") != "success":
        return None
    results = payload.get("data", {}).get("result") or []
    if not results:
        return None
    best: float | None = None
    for row in results:
        value = row.get("value") if isinstance(row, dict) else None
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        try:
            n = float(value[1])
        except (TypeError, ValueError):
            continue
        best = n if best is None else max(best, n)
    return best


def collect_load() -> dict[str, float]:
    out: dict[str, float] = {}
    mapping = (("cpu", _CPU_QUERY), ("memory", _MEM_QUERY), ("disk", _DISK_QUERY))
    for name, query in mapping:
        n = _prometheus_scalar(query)
        if n is None:
            continue
        out[name] = n
    return out


def _incident(ident: str, severity: str, title: str, detail: str) -> dict[str, str]:
    return {"id": ident, "severity": severity, "title": title, "detail": detail}


def build_incidents(
    checks: list[dict[str, Any]],
    git_checks: list[dict[str, Any]],
    load: dict[str, float] | None = None,
    login: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    by_name = {c.get("name"): c for c in checks if isinstance(c, dict)}
    git_by = {c.get("name"): c for c in git_checks if isinstance(c, dict)}

    sites = by_name.get("sites")
    if sites and sites.get("ok") is False:
        out.append(_incident("sites", "error", "Sites down", str(sites.get("detail") or "")))

    tls = by_name.get("tls")
    if tls and tls.get("ok") is False:
        detail = str(tls.get("detail") or "")
        for part in [p.strip() for p in detail.split(";") if p.strip()]:
            sev = "error" if "expired" in part or "unreadable" in part else "warn"
            out.append(_incident(f"tls:{part}", sev, "Certificate", part))

    prom = by_name.get("prometheus_targets")
    if prom and prom.get("ok") is False:
        out.append(
            _incident("prometheus", "error", "Prometheus targets", str(prom.get("detail") or ""))
        )

    git_http = git_by.get("git_http")
    if git_http and git_http.get("ok") is False:
        out.append(_incident("git", "error", "Git down", str(git_http.get("detail") or "")))

    git_prom = git_by.get("peer_prometheus_targets")
    if git_prom and git_prom.get("ok") is False:
        out.append(
            _incident("git-prometheus", "warn", "Git Prometheus", str(git_prom.get("detail") or ""))
        )

    login = login if login is not None else request_limits.login_failure_summary()
    count = int(login.get("count") or 0)
    if count > 0:
        last_ip = str(login.get("last_ip") or "")
        ips = int(login.get("ips") or 0)
        detail = f"{count} failed login(s), {ips} IP(s)"
        if last_ip:
            detail += f", last {last_ip}"
        out.append(_incident("login", "warn", "Failed login", detail))

    load = load if load is not None else collect_load()
    warn_pct = float(load_warn_pct())
    error_pct = float(load_error_pct())
    labels = {"cpu": "CPU", "memory": "Memory", "disk": "Disk"}
    for key, title in labels.items():
        if key not in load:
            continue
        pct = load[key]
        detail = f"{title} {pct:.0f}%"
        if pct >= error_pct:
            out.append(_incident(f"load:{key}", "error", f"{title} load", detail))
        elif pct >= warn_pct:
            out.append(_incident(f"load:{key}", "warn", f"{title} load", detail))

    out.sort(key=lambda row: (0 if row["severity"] == "error" else 1, row["title"], row["id"]))
    return out


def run_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if _env_bool("HEALTH_CHECK_PROMETHEUS", True):
        checks.append(check_prometheus_targets())
    if _env_bool("HEALTH_CHECK_SITES", True):
        checks.append(check_sites_up())
    if _env_bool("HEALTH_CHECK_TLS", True):
        checks.append(check_tls())
    return checks


def run_git_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if _env_bool("HEALTH_CHECK_GIT", False) and git_probe_url():
        checks.append(check_prometheus_targets(peer_only=True))
        checks.append(check_git_http())
    return checks


def _overall_from_checks(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "unknown"
    return "ok" if all(c["ok"] for c in checks) else "down"


def _is_git_incident(row: dict[str, str]) -> bool:
    ident = str(row.get("id") or "")
    return ident == "git" or ident.startswith("git-")


def _overall_from_incidents(incidents: list[dict[str, str]]) -> str:
    if any(
        row.get("severity") == "error" and not _is_git_incident(row) for row in incidents
    ):
        return "down"
    return "ok"


def _finalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    incidents = [row for row in list(payload.get("incidents") or []) if row.get("id") != "login"]
    incidents.extend(
        build_incidents([], [], load={}, login=request_limits.login_failure_summary())
    )
    incidents.sort(key=lambda row: (0 if row["severity"] == "error" else 1, row["title"], row["id"]))
    out["incidents"] = incidents
    out["overall"] = _overall_from_incidents(incidents)
    return out


def health_payload(*, force_refresh: bool = False) -> dict[str, Any]:
    global _health_cache, _health_cache_at
    now = time.time()
    ttl = health_cache_ttl_seconds()
    with _health_lock:
        if (
            not force_refresh
            and _health_cache is not None
            and now - _health_cache_at < ttl
        ):
            return _finalize_payload(_health_cache)

    with _refresh_lock:
        now = time.time()
        with _health_lock:
            if (
                not force_refresh
                and _health_cache is not None
                and now - _health_cache_at < ttl
            ):
                return _finalize_payload(_health_cache)
        checks = run_checks()
        git_checks = run_git_checks()
        fail2ban = fail2ban_payload()
        incidents = build_incidents(checks, git_checks)
        payload = {
            "overall": _overall_from_incidents(incidents),
            "checks": checks,
            "git": {"overall": _overall_from_checks(git_checks), "checks": git_checks},
            "fail2ban": fail2ban,
            "incidents": incidents,
        }
        with _health_lock:
            _health_cache = payload
            _health_cache_at = time.time()
        return _finalize_payload(payload)
