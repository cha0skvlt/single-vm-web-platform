"""SITES-1 — Traefik router domains + HTTP probes for /sites."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

_HOST_RE = re.compile(r"Host\(`([^`]+)`\)")

_cache_lock = threading.Lock()
_refresh_lock = threading.Lock()
_cache_rows: list[dict[str, str]] | None = None
_cache_at: float = 0.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def traefik_api_url() -> str:
    return os.environ.get("TRAEFIK_API", "http://traefik:8080").rstrip("/")


def traefik_probe_url() -> str:
    return os.environ.get("TRAEFIK_PROBE_URL", "http://traefik:80").rstrip("/")


def probe_timeout_seconds() -> float:
    return float(_env_int("SITE_PROBE_TIMEOUT", 3))


def status_ttl_seconds() -> int:
    return _env_int("SITE_STATUS_TTL", 15)


def probe_concurrency() -> int:
    return max(1, min(_env_int("SITE_PROBE_CONCURRENCY", 8), 32))


def extract_host_domains(rule: str) -> list[str]:
    if not rule:
        return []
    return [m.group(1).strip().lower() for m in _HOST_RE.finditer(rule) if m.group(1).strip()]


def domains_from_routers(routers: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for router in routers:
        if not isinstance(router, dict):
            continue
        rule = router.get("rule") or ""
        if not isinstance(rule, str):
            continue
        for domain in extract_host_domains(rule):
            found.add(domain)
    return sorted(found)


def fetch_traefik_routers(base_url: str | None = None) -> list[dict[str, Any]]:
    api = (base_url or traefik_api_url()).rstrip("/")
    url = f"{api}/api/http/routers"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, list) and payload:
            return payload
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass
    return []


def classify_probe_status(http_status: int | None, error: bool) -> str:
    if error or http_status is None:
        return "down"
    if 200 <= http_status < 400:
        return "up"
    if http_status >= 500:
        return "down"
    return "unknown"


def probe_domain(
    domain: str,
    *,
    probe_base: str | None = None,
    timeout: float | None = None,
) -> str:
    base = (probe_base or traefik_probe_url()).rstrip("/")
    url = f"{base}/"
    req = urllib.request.Request(url, method="HEAD", headers={"Host": domain})
    try:
        with urllib.request.urlopen(req, timeout=timeout or probe_timeout_seconds()) as resp:
            return classify_probe_status(resp.status, error=False)
    except urllib.error.HTTPError as exc:
        return classify_probe_status(exc.code, error=False)
    except (urllib.error.URLError, TimeoutError, OSError):
        return "down"


def site_url(domain: str) -> str:
    return f"https://{domain}"


def checked_at_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_site_row(domain: str, status: str) -> dict[str, str]:
    return {
        "domain": domain,
        "url": site_url(domain),
        "status": status,
        "checked_at": checked_at_iso(),
    }


def probe_domains(
    domains: list[str],
    *,
    probe_base: str | None = None,
    timeout: float | None = None,
    concurrency: int | None = None,
) -> list[dict[str, str]]:
    if not domains:
        return []
    workers = concurrency or probe_concurrency()
    rows: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                probe_domain,
                domain,
                probe_base=probe_base,
                timeout=timeout,
            ): domain
            for domain in domains
        }
        for fut in as_completed(futures):
            domain = futures[fut]
            status = fut.result()
            rows.append(build_site_row(domain, status))
    rows.sort(key=lambda r: r["domain"])
    return rows


def collect_sites(
    *,
    traefik_api: str | None = None,
    probe_base: str | None = None,
    timeout: float | None = None,
    concurrency: int | None = None,
) -> list[dict[str, str]]:
    routers = fetch_traefik_routers(traefik_api)
    domains = domains_from_routers(routers)
    return probe_domains(
        domains,
        probe_base=probe_base,
        timeout=timeout,
        concurrency=concurrency,
    )


def sites_list(
    *,
    traefik_api: str | None = None,
    probe_base: str | None = None,
    force_refresh: bool = False,
) -> list[dict[str, str]]:
    global _cache_rows, _cache_at
    now = time.time()
    ttl = status_ttl_seconds()
    with _cache_lock:
        if (
            not force_refresh
            and _cache_rows is not None
            and now - _cache_at < ttl
        ):
            return list(_cache_rows)

    with _refresh_lock:
        now = time.time()
        with _cache_lock:
            if _cache_rows is not None and now - _cache_at < ttl:
                return list(_cache_rows)
        try:
            rows = collect_sites(traefik_api=traefik_api, probe_base=probe_base)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            with _cache_lock:
                if _cache_rows is not None:
                    return list(_cache_rows)
            return []
        except Exception:
            with _cache_lock:
                if _cache_rows is not None:
                    return list(_cache_rows)
            raise
        with _cache_lock:
            _cache_rows = rows
            _cache_at = time.time()
        return list(rows)


def reset_cache_for_tests() -> None:
    global _cache_rows, _cache_at
    with _cache_lock:
        _cache_rows = None
        _cache_at = 0.0
