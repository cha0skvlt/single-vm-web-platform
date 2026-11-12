"""LOG-3 — diagnostic NDJSON bundle (manifest + snapshots + redacted logs)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TextIO

from scripts.diagnostic_redact import REDACTED, redact_dict, redact_string
from scripts.inventory import parse_inventory_connection
from scripts.project import ENV_KEY, app_install_root, install_root, project_hostname_or

SCHEMA_VERSION = "2"
LOG_EVENT_SCHEMA_VERSION = "1"
DEFAULT_HOURS = 6
DEFAULT_MAX_MB = 32
DEFAULT_MAX_INPUT_LINES = 100_000
PANEL_MAX_INPUT_LINES = 2500
DEFAULT_LOKI_LIMIT = 5000
PANEL_LOKI_LIMIT = 500
MAX_CMD_OUTPUT_BYTES = 256_000
COMPACT_LEVELS = frozenset({"warn", "error", "critical"})
SNAPSHOT_TYPES = frozenset(
    {
        "manifest",
        "system",
        "certificates",
        "health",
        "sites",
        "fail2ban",
        "prometheus",
        "traefik",
        "node",
        "compose",
    }
)
LOG_LEVEL_RE = re.compile(r'"(?:level|Level|PRIORITY)"\s*:\s*"?([^",}\s]+)"?', re.I)
HOST_OS_RELEASE = Path("/host/etc/os-release")


def utc_now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def is_snapshot_record(obj: dict[str, Any]) -> bool:
    return obj.get("type") in SNAPSHOT_TYPES


def docker_available() -> bool:
    sock = Path("/var/run/docker.sock")
    return sock.is_socket() and shutil.which("docker") is not None


def detect_runtime() -> str:
    explicit = os.environ.get("DIAGNOSTIC_RUNTIME", "").strip().lower()
    if explicit in ("panel", "host"):
        return explicit
    if docker_available():
        return "host"
    return "panel"


def run_cmd(cmd: list[str], *, timeout: int = 30, cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_CMD_OUTPUT_BYTES:
        out = out[:MAX_CMD_OUTPUT_BYTES] + "\n...truncated..."
    return out.strip() or f"exit={proc.returncode}"


def read_deploy_sha(repo_root: Path) -> str:
    marker = repo_root / ".deploy-sha"
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()[:64] or "unknown"
    if shutil.which("git"):
        sha = run_cmd(["git", "-C", str(repo_root), "rev-parse", "HEAD"], timeout=10)
        if sha and not sha.startswith("error:") and not sha.startswith("exit="):
            return sha
    return "unknown"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _panel_app_dir(app_root: Path) -> Path:
    return app_root / "sites/ops-panel/app"


def _ensure_panel_path(app_root: Path) -> bool:
    panel = _panel_app_dir(app_root)
    if not panel.is_dir():
        return False
    panel_str = str(panel)
    if panel_str not in sys.path:
        sys.path.insert(0, panel_str)
    return True


def _prometheus_base() -> str:
    return os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")


def _prometheus_json(path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    base = _prometheus_base()
    query = urllib.parse.urlencode(params or {})
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception as exc:  # noqa: BLE001 — diagnostic must not fail
        return {"status": "error", "error": str(exc), "url": url}
    return {"status": "error", "error": "invalid prometheus response", "url": url}


def _traefik_base() -> str:
    return os.environ.get("TRAEFIK_API", "http://traefik:8080").rstrip("/")


def _traefik_json(resource: str) -> dict[str, Any] | list[Any]:
    url = f"{_traefik_base()}/api/http/{resource}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, (dict, list)):
            return payload
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "url": url}
    return {"error": "invalid traefik response", "url": url}


def collect_manifest(
    *,
    app_root: Path,
    repo_root: Path,
    hours: int,
    full: bool,
    runtime: str | None = None,
) -> dict[str, Any]:
    runtime = runtime or detect_runtime()
    manifest: dict[str, Any] = {
        "type": "manifest",
        "ts": utc_now(),
        "schema_version": SCHEMA_VERSION,
        "bundle_mode": "full" if full else "compact",
        "log_hours": hours,
        "runtime": runtime,
        "hostname": socket.gethostname(),
        "project_hostname": os.environ.get("PROJECT_HOSTNAME", "").strip(),
        "prod_public_host": os.environ.get("PROD_PUBLIC_HOST", "").strip(),
        "deploy_sha": read_deploy_sha(repo_root),
        "capabilities": {
            "docker": docker_available(),
            "openssl": shutil.which("openssl") is not None,
            "fail2ban_client": shutil.which("fail2ban-client") is not None,
            "journalctl": shutil.which("journalctl") is not None,
            "git": shutil.which("git") is not None,
            "terraform": shutil.which("terraform") is not None,
            "panel_app_mounted": _panel_app_dir(app_root).is_dir(),
            "host_os_release_mounted": HOST_OS_RELEASE.is_file(),
        },
        "log_sources": _log_source_names(runtime),
    }
    return redact_dict(manifest)


def _log_source_names(runtime: str) -> list[str]:
    if runtime == "panel":
        return ["loki"]
    names = []
    if docker_available():
        names.append("docker_logs")
    if shutil.which("journalctl"):
        names.append("journald")
    return names or ["none"]


def collect_snapshot_records(
    *,
    app_root: Path,
    repo_root: Path,
    runtime: str | None = None,
) -> list[dict[str, Any]]:
    runtime = runtime or detect_runtime()
    records: list[dict[str, Any]] = [
        collect_system_snapshot(runtime=runtime, app_root=app_root, repo_root=repo_root),
        collect_certificates_snapshot(app_root),
        collect_health_snapshot(app_root),
        collect_sites_snapshot(app_root),
        collect_fail2ban_snapshot(app_root),
        collect_prometheus_snapshot(),
        collect_traefik_snapshot(),
        collect_node_snapshot(),
    ]
    if runtime == "host" and docker_available():
        records.append(collect_compose_snapshot(app_root))
    return [redact_dict(record) for record in records]


def collect_system_snapshot(
    *,
    runtime: str,
    app_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "system",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "runtime": runtime,
        "kernel": run_cmd(["uname", "-a"]),
        "container_os_release": _read_text(Path("/etc/os-release")).strip(),
    }
    host_os = _read_text(HOST_OS_RELEASE).strip()
    if host_os:
        record["host_os_release"] = host_os
    if runtime == "host":
        record["docker_version"] = run_cmd(["docker", "version", "--format", "json"], timeout=15)
        record["terraform_version"] = run_cmd(["terraform", "version"], timeout=15)
        record["terraform_providers"] = _terraform_providers(repo_root)
        record["disk"] = _disk_stats()
        record["memory"] = _memory_stats()
        record["ss_tlnp"] = run_cmd(
            ["sh", "-c", "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || true"]
        )
    else:
        record["note"] = "Panel runtime: host metrics in node snapshot; logs from Loki"
    return record


def collect_certificates_snapshot(app_root: Path) -> dict[str, Any]:
    return {
        "type": "certificates",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "items": _cert_expiry(app_root),
    }


def collect_health_snapshot(app_root: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "health",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
    }
    if not _ensure_panel_path(app_root):
        record["error"] = "ops-panel app not mounted"
        record["overall"] = "unknown"
        record["checks"] = []
        return record
    try:
        from health_status import health_payload

        record.update(health_payload(force_refresh=True))
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)
        record["overall"] = "unknown"
        record["checks"] = []
    return record


def collect_sites_snapshot(app_root: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "sites",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "rows": [],
    }
    if not _ensure_panel_path(app_root):
        record["error"] = "ops-panel app not mounted"
        return record
    try:
        from sites_status import sites_list

        record["rows"] = sites_list(force_refresh=True)
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)
    return record


def collect_fail2ban_snapshot(app_root: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "fail2ban",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
    }
    sock = os.environ.get("FAIL2BAN_SOCKET", "/var/run/fail2ban/fail2ban.sock").strip()
    if sock and Path(sock).exists():
        record["client_status"] = run_cmd(
            ["fail2ban-client", "-s", sock, "status"],
            timeout=15,
        )
    else:
        record["client_status"] = "socket unavailable"
    if _ensure_panel_path(app_root):
        try:
            from fail2ban_status import fail2ban_payload

            record["panel"] = fail2ban_payload()
        except Exception as exc:  # noqa: BLE001
            record["panel_error"] = str(exc)
    else:
        record["panel_error"] = "ops-panel app not mounted"
    return record


def collect_prometheus_snapshot() -> dict[str, Any]:
    targets_payload = _prometheus_json("/api/v1/targets")
    data = targets_payload.get("data") if isinstance(targets_payload, dict) else {}
    active = data.get("activeTargets") if isinstance(data, dict) else []
    if not isinstance(active, list):
        active = []
    down: list[dict[str, Any]] = []
    up_count = 0
    for target in active:
        if not isinstance(target, dict):
            continue
        labels = target.get("labels") if isinstance(target.get("labels"), dict) else {}
        health = target.get("health")
        row = {
            "job": labels.get("job", ""),
            "instance": labels.get("instance", ""),
            "health": health,
            "lastError": target.get("lastError", ""),
            "lastScrape": target.get("lastScrape", ""),
        }
        if health == "up":
            up_count += 1
        else:
            down.append(row)
    up_query = _prometheus_json("/api/v1/query", params={"query": "up"})
    return {
        "type": "prometheus",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "url": _prometheus_base(),
        "targets_total": len(active),
        "targets_up": up_count,
        "targets_down": down,
        "up_query": up_query,
    }


def collect_traefik_snapshot() -> dict[str, Any]:
    routers = _summarize_traefik_routers(_traefik_json("routers"))
    services = _summarize_traefik_services(_traefik_json("services"))
    middlewares = _summarize_traefik_middlewares(_traefik_json("middlewares"))
    return {
        "type": "traefik",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "url": _traefik_base(),
        "routers": routers,
        "services": services,
        "middlewares": middlewares,
    }


def _summarize_traefik_routers(data: Any) -> Any:
    if isinstance(data, dict) and data.get("error"):
        return data
    if not isinstance(data, list):
        return {"error": "unexpected traefik routers payload"}
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "name": item.get("name"),
                "rule": item.get("rule"),
                "entryPoints": item.get("entryPoints"),
                "service": item.get("service"),
                "middlewares": item.get("middlewares"),
                "status": item.get("status"),
                "provider": item.get("provider"),
            }
        )
    return rows


def _summarize_traefik_services(data: Any) -> Any:
    if isinstance(data, dict) and data.get("error"):
        return data
    if not isinstance(data, list):
        return {"error": "unexpected traefik services payload"}
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        load_balancer = item.get("loadBalancer") or {}
        servers = load_balancer.get("servers") or []
        rows.append(
            {
                "name": item.get("name"),
                "provider": item.get("provider"),
                "status": item.get("status"),
                "server_count": len(servers) if isinstance(servers, list) else 0,
            }
        )
    return rows


def _summarize_traefik_middlewares(data: Any) -> Any:
    if isinstance(data, dict) and data.get("error"):
        return data
    if not isinstance(data, list):
        return {"error": "unexpected traefik middlewares payload"}
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {
            "name": item.get("name"),
            "type": item.get("type"),
            "provider": item.get("provider"),
            "status": item.get("status"),
            "usedBy": item.get("usedBy"),
        }
        mw_type = str(item.get("type") or "").lower()
        if mw_type == "forwardauth":
            fa = item.get("forwardAuth") or {}
            row["forwardAuth"] = {
                "address": fa.get("address"),
                "maxBodySize": fa.get("maxBodySize"),
                "maxResponseBodySize": fa.get("maxResponseBodySize"),
                "authRequestHeaders": REDACTED if fa.get("authRequestHeaders") else None,
            }
        elif mw_type == "headers":
            headers = item.get("headers") or {}
            if headers.get("customRequestHeaders"):
                row["headers"] = {"customRequestHeaders": REDACTED}
        rows.append(row)
    return rows


def _prometheus_scalar(query: str) -> float | None:
    payload = _prometheus_json("/api/v1/query", params={"query": query})
    if payload.get("status") != "success":
        return None
    results = payload.get("data", {}).get("result", [])
    if not results:
        return None
    value = results[0].get("value")
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


def collect_node_snapshot() -> dict[str, Any]:
    metrics = {
        "mem_available_bytes": _prometheus_scalar("node_memory_MemAvailable_bytes"),
        "mem_total_bytes": _prometheus_scalar("node_memory_MemTotal_bytes"),
        "disk_avail_bytes": _prometheus_scalar(
            'node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"}'
        ),
        "disk_size_bytes": _prometheus_scalar(
            'node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"}'
        ),
        "load1": _prometheus_scalar("node_load1"),
        "uptime_seconds": None,
    }
    boot = _prometheus_scalar("node_boot_time_seconds")
    now = _prometheus_scalar("node_time_seconds")
    if boot is not None and now is not None:
        metrics["uptime_seconds"] = now - boot
    return {
        "type": "node",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "metrics": metrics,
    }


def compose_stack_files(app_root: Path) -> list[tuple[str, Path]]:
    stacks: list[tuple[str, Path]] = [("base", app_root / "docker-compose.yml")]
    prod_status = app_root / "sites/ops-panel/docker-compose.yml"
    if prod_status.is_file():
        stacks.append(("ops-panel", prod_status))
    sites_dir = app_root / "sites"
    if sites_dir.is_dir():
        for compose_file in sorted(sites_dir.glob("*/docker-compose.yml")):
            name = compose_file.parent.name
            if name.startswith("_") or name == "ops-panel":
                continue
            stacks.append((name, compose_file))
    return stacks


def collect_compose_snapshot(app_root: Path) -> dict[str, Any]:
    env_file = app_root / ".env"
    stacks: list[dict[str, Any]] = []
    for name, compose_file in compose_stack_files(app_root):
        if not compose_file.is_file():
            continue
        ps_text = run_cmd(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "ps",
                "--format",
                "json",
            ],
            timeout=60,
            cwd=app_root,
        )
        images_text = run_cmd(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "images",
                "--format",
                "json",
            ],
            timeout=60,
            cwd=app_root,
        )
        stacks.append(
            {
                "name": name,
                "compose_file": str(compose_file.relative_to(app_root)),
                "ps": ps_text,
                "images": _parse_compose_json_lines(images_text),
            }
        )
    containers = run_cmd(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=30)
    return {
        "type": "compose",
        "schema_version": SCHEMA_VERSION,
        "ts": utc_now(),
        "stacks": stacks,
        "containers": _parse_compose_json_lines(containers),
        "image_digests": _image_digests(app_root),
    }


def _parse_compose_json_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _terraform_providers(repo_root: Path) -> dict[str, str]:
    tf_dir = repo_root / "terraform"
    if not tf_dir.is_dir():
        return {}
    text = run_cmd(["terraform", "-chdir", str(tf_dir), "providers"], timeout=30)
    providers: dict[str, str] = {}
    for line in text.splitlines():
        if "provider[" in line:
            providers[line.strip()] = "present"
    return providers


def _image_digests(app_root: Path) -> list[dict[str, str]]:
    if not docker_available():
        return []
    text = run_cmd(
        ["docker", "compose", "--env-file", ".env", "images", "--format", "json"],
        timeout=60,
        cwd=app_root,
    )
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(
                {
                    "repository": str(row.get("Repository", "")),
                    "tag": str(row.get("Tag", "")),
                    "id": str(row.get("ID", "")),
                }
            )
    return rows


def _disk_stats() -> dict[str, str]:
    text = run_cmd(["df", "-hP", "/"])
    return {"df_root": text}


def _memory_stats() -> dict[str, str]:
    return {"free": run_cmd(["sh", "-c", "free -h 2>/dev/null || true"])}


def _cert_expiry(app_root: Path) -> list[dict[str, str]]:
    certs_root = app_root / "certs"
    out: list[dict[str, str]] = []
    if not certs_root.is_dir():
        return out
    for pem in sorted(certs_root.glob("*/fullchain.pem")):
        domain = pem.parent.name
        text = run_cmd(
            [
                "openssl",
                "x509",
                "-in",
                str(pem),
                "-noout",
                "-enddate",
            ],
            timeout=10,
        )
        out.append({"domain": domain, "not_after": text.replace("notAfter=", "").strip()})
    return out


def infer_level(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                level = data.get("level")
                if isinstance(level, str) and level:
                    return level.lower()
        except json.JSONDecodeError:
            pass
    m = LOG_LEVEL_RE.search(stripped)
    if m:
        return m.group(1).lower()
    lowered = stripped.lower()
    if any(x in lowered for x in ("error", "fatal", "crit", "panic")):
        return "error"
    if "warn" in lowered:
        return "warn"
    return "info"


def should_include_line(line: str, *, full: bool, compact_tail: int, line_index: int, total: int) -> bool:
    if full:
        return True
    level = infer_level(line)
    if level in COMPACT_LEVELS:
        return True
    return line_index >= max(0, total - compact_tail)


def normalize_log_event(line: str, *, source: str) -> dict[str, Any]:
    level = infer_level(line)
    event = {
        "ts": utc_now(),
        "level": level,
        "service": source,
        "event": "log.line",
        "msg": line[:8000],
        "attrs": {"source": source},
        "schema_version": LOG_EVENT_SCHEMA_VERSION,
    }
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and data.get("schema_version") == LOG_EVENT_SCHEMA_VERSION:
                return data
        except json.JSONDecodeError:
            pass
    return event


def _loki_limit(*, runtime: str) -> int:
    raw = os.environ.get("DIAGNOSTIC_LOKI_LIMIT", "").strip()
    if not raw:
        return PANEL_LOKI_LIMIT if runtime == "panel" else DEFAULT_LOKI_LIMIT
    try:
        return max(100, min(int(raw), 5000))
    except ValueError:
        return PANEL_LOKI_LIMIT if runtime == "panel" else DEFAULT_LOKI_LIMIT


def _diagnostic_logql(*, full: bool) -> str:
    if full:
        return '{service=~".+"}'
    return '{service=~".+",level=~"warn|error|critical"}'


def iter_loki_logs(
    hours: int,
    *,
    app_root: Path,
    full: bool,
    runtime: str = "panel",
    max_entries: int | None = None,
) -> Iterator[tuple[str, str]]:
    if not _ensure_panel_path(app_root):
        return
    try:
        from loki_stream import extract_entries, query_range
    except ImportError:
        return

    end_ns = int(time.time() * 1_000_000_000)
    start_ns = end_ns - hours * 3_600 * 1_000_000_000
    logql = _diagnostic_logql(full=full)
    limit = _loki_limit(runtime=runtime)
    chunk_ns = 3_600 * 1_000_000_000
    cursor = start_ns
    emitted = 0
    while cursor < end_ns:
        if max_entries is not None and emitted >= max_entries:
            return
        chunk_end = min(end_ns, cursor + chunk_ns)
        try:
            payload = query_range(
                logql=logql,
                start_ns=cursor,
                end_ns=chunk_end,
                limit=limit,
            )
        except ValueError:
            break
        entries = extract_entries(payload)
        if not entries:
            cursor = chunk_end + 1
            continue
        for entry in entries:
            if max_entries is not None and emitted >= max_entries:
                return
            service = entry.get("service", "unknown")
            line = json.dumps(
                {
                    "ts": entry.get("ts", utc_now()),
                    "level": entry.get("level", "info"),
                    "service": service,
                    "event": "log.line",
                    "msg": entry.get("msg", "")[:8000],
                    "attrs": {"source": "loki"},
                    "schema_version": LOG_EVENT_SCHEMA_VERSION,
                },
                separators=(",", ":"),
            )
            yield str(service), line
            emitted += 1
        last_ns = entries[-1].get("_ts_ns")
        if last_ns is None:
            cursor = chunk_end + 1
            continue
        try:
            cursor = max(cursor + 1, int(last_ns) + 1)
        except (TypeError, ValueError):
            cursor = chunk_end + 1


def iter_docker_logs(hours: int, *, app_root: Path) -> Iterator[tuple[str, str]]:
    if not docker_available():
        return
    since = f"{hours}h"
    ids_text = run_cmd(["docker", "ps", "-q"], timeout=30)
    if ids_text.startswith("error:") or ids_text.startswith("exit="):
        return
    for cid in ids_text.split():
        name = run_cmd(["docker", "inspect", "--format", "{{.Name}}", cid], timeout=10).lstrip("/")
        if name.startswith("error:"):
            continue
        service = _service_from_container(name)
        label = run_cmd(
            ["docker", "inspect", "--format", "{{index .Config.Labels \"com.docker.compose.service\"}}", cid],
            timeout=10,
        )
        if label and not label.startswith("error:") and label != "<no value>":
            service = label
        text = run_cmd(["docker", "logs", "--since", since, "--timestamps", cid], timeout=120)
        if text.startswith("error:"):
            continue
        for line in text.splitlines():
            if line.strip():
                yield service, line


def iter_journal_logs(hours: int) -> Iterator[tuple[str, str]]:
    if not shutil.which("journalctl"):
        return
    since = f"{hours}h ago"
    text = run_cmd(
        ["journalctl", "--since", since, "--no-pager", "-o", "short-iso"],
        timeout=120,
    )
    if text.startswith("error:"):
        return
    for line in text.splitlines():
        if line.strip():
            yield "journald", line


def default_log_sources(
    *,
    runtime: str,
    app_root: Path,
    full: bool,
) -> Callable[[int], Iterable[tuple[str, str]]]:
    if runtime == "panel":

        def panel_sources(hours: int) -> Iterable[tuple[str, str]]:
            yield from iter_loki_logs(
                hours,
                app_root=app_root,
                full=full,
                runtime="panel",
                max_entries=max_input_lines(runtime="panel", full=full),
            )

        return panel_sources

    def host_sources(hours: int) -> Iterable[tuple[str, str]]:
        yield from iter_docker_logs(hours, app_root=app_root)
        yield from iter_journal_logs(hours)

    return host_sources


def max_input_lines(*, runtime: str | None = None, full: bool = False) -> int:
    raw = os.environ.get("DIAGNOSTIC_MAX_INPUT_LINES", "").strip()
    if not raw:
        if runtime == "panel" and not full:
            return PANEL_MAX_INPUT_LINES
        return DEFAULT_MAX_INPUT_LINES
    try:
        return max(1, int(raw))
    except ValueError:
        if runtime == "panel" and not full:
            return PANEL_MAX_INPUT_LINES
        return DEFAULT_MAX_INPUT_LINES


def _bounded_log_lines(
    log_sources: Callable[[int], Iterable[tuple[str, str]]],
    hours: int,
    *,
    max_lines: int | None = None,
) -> Iterator[tuple[str, str]]:
    cap = max_lines if max_lines is not None else max_input_lines()
    count = 0
    for source, raw in log_sources(hours):
        if raw.strip():
            yield source, raw
            count += 1
            if count >= cap:
                break


def _count_log_lines(
    log_sources: Callable[[int], Iterable[tuple[str, str]]],
    hours: int,
    *,
    max_lines: int | None = None,
) -> int:
    return sum(1 for _ in _bounded_log_lines(log_sources, hours, max_lines=max_lines))


def _service_from_container(name: str) -> str:
    m = re.match(r"(?:[^-]+-)?([^-]+)-\d+$", name)
    if m:
        return m.group(1)
    return name or "docker"


def write_bundle(
    output: Path,
    *,
    app_root: Path,
    repo_root: Path,
    hours: int = DEFAULT_HOURS,
    full: bool = False,
    max_bytes: int = DEFAULT_MAX_MB * 1024 * 1024,
    log_sources: Callable[[int], Iterable[tuple[str, str]]] | None = None,
    runtime: str | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = runtime or detect_runtime()
    manifest = collect_manifest(
        app_root=app_root,
        repo_root=repo_root,
        hours=hours,
        full=full,
        runtime=runtime,
    )
    snapshots = collect_snapshot_records(app_root=app_root, repo_root=repo_root, runtime=runtime)

    if log_sources is None:
        log_sources = default_log_sources(runtime=runtime, app_root=app_root, full=full)

    line_cap = max_input_lines(runtime=runtime, full=full)
    if full:
        total = line_cap
    else:
        total = _count_log_lines(log_sources, hours, max_lines=line_cap)
    compact_tail = 500

    written = 0
    with output.open("w", encoding="utf-8") as fh:
        written += _write_record(fh, manifest)

        for snap in snapshots:
            if written >= max_bytes:
                break
            written += _write_record(fh, snap)

        for idx, (source, raw) in enumerate(
            _bounded_log_lines(log_sources, hours, max_lines=line_cap)
        ):
            if written >= max_bytes:
                break
            if not should_include_line(
                raw,
                full=full,
                compact_tail=compact_tail,
                line_index=idx,
                total=total,
            ):
                continue
            safe = redact_string(raw)
            event = normalize_log_event(safe, source=source)
            event = redact_dict(event)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            if written + len(line) + 1 > max_bytes:
                break
            fh.write(line + "\n")
            written += len(line) + 1

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    Path(f"{output}.sha256").write_text(digest + "\n", encoding="utf-8")
    return output


def _write_record(fh: TextIO, obj: dict[str, Any]) -> int:
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    fh.write(line)
    return len(line)


def default_paths() -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root, repo_root / "app"


def build_output_name(host: str | None = None) -> str:
    name = (host or os.environ.get("PROD_PUBLIC_HOST") or socket.gethostname()).split(".")[0]
    return f"diagnostic-{name}-{utc_stamp()}.ndjson"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build diagnostic NDJSON bundle (LOG-3)")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("-o", "--output", type=Path, help="Output .ndjson path")
    parser.add_argument("--stdout", action="store_true", help="Write bundle to stdout")
    parser.add_argument("--max-mb", type=int, default=DEFAULT_MAX_MB)
    parser.add_argument("--fetch", action="store_true", help="SSH from inventory and save locally")
    args = parser.parse_args(argv)

    if args.fetch:
        return _fetch_remote(args)

    repo_root, app_root = default_paths()
    if args.stdout and args.output is None:
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".ndjson", prefix="diagnostic-", dir="/tmp")
        os.close(fd)
        out = Path(tmp)
    else:
        out = args.output or Path(build_output_name())
    write_bundle(
        out,
        app_root=app_root,
        repo_root=repo_root,
        hours=args.hours,
        full=args.full,
        max_bytes=args.max_mb * 1024 * 1024,
    )
    if args.stdout:
        sys.stdout.write(out.read_text(encoding="utf-8"))
        if str(out).startswith("/tmp/"):
            out.unlink(missing_ok=True)
            sha = Path(f"{out}.sha256")
            sha.unlink(missing_ok=True)
    else:
        print(out)
        print(out.with_suffix(out.suffix + ".sha256"))
    return 0


def _fetch_remote(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    inv = repo_root / "ansible/inventory.ini"
    if not inv.is_file():
        print("error: ansible/inventory.ini required for --fetch", file=sys.stderr)
        return 1
    try:
        host, port, user = parse_inventory_connection(inv.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hostname = os.environ.get(ENV_KEY, "").strip() or project_hostname_or()
    remote_root = install_root(hostname=hostname)
    remote_out = f"/tmp/{build_output_name(host)}"
    flags = f"--hours {args.hours}"
    if args.full:
        flags += " --full"
    remote_cmd = (
        f"cd {remote_root} && PROJECT_HOSTNAME={hostname} DIAGNOSTIC_RUNTIME=host "
        f"python3 -m scripts.diagnostic {flags} -o {remote_out}"
    )
    ssh = ["ssh", "-p", str(port), f"{user}@{host}", remote_cmd]
    if subprocess.run(ssh, check=False).returncode != 0:
        return 1
    local = Path(build_output_name(host))
    scp = ["scp", "-P", str(port), f"{user}@{host}:{remote_out}", str(local)]
    if subprocess.run(scp, check=False).returncode != 0:
        return 1
    print(local)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
