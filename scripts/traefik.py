"""Traefik Host() label checks."""

from __future__ import annotations

import re
from pathlib import Path

HOST_RULE = re.compile(r"Host\(`([^`]+)`\)")


def parse_host_rule(rule: str) -> str | None:
    if not rule:
        return None
    match = HOST_RULE.search(rule)
    if not match:
        return None
    host = match.group(1)
    if host.startswith("${"):
        return None
    return host


def resolve_prod_public_host(
    *,
    remote_env: str = "",
    traefik_rule: str = "",
    local: str = "",
    default: str = "status.example.com",
) -> tuple[str, list[str]]:
    """Pick prod panel Host() for smoke checks (server truth over local app/.env)."""
    from scripts.envfile import read_env_value

    warnings: list[str] = []
    local = local.strip()

    host = read_env_value(remote_env, "PROD_PUBLIC_HOST")
    if host:
        if local and local != host:
            warnings.append(
                f"local PROD_PUBLIC_HOST={local} != server .env {host}; using server .env"
            )
        return host, warnings

    host = parse_host_rule(traefik_rule)
    if host:
        warnings.append(
            f"PROD_PUBLIC_HOST missing in server .env; using Traefik label Host(`{host}`)"
        )
        if local and local != host:
            warnings.append(f"local PROD_PUBLIC_HOST={local} != Traefik label {host}")
        return host, warnings

    if local:
        warnings.append(f"PROD_PUBLIC_HOST missing on server; using local {local}")
        return local, warnings

    warnings.append(f"PROD_PUBLIC_HOST unset everywhere; using default {default}")
    return default, warnings


def collect_host_rules(paths: list[Path]) -> list[str]:
    hosts: list[str] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            for host in HOST_RULE.findall(line):
                if host.startswith("${"):
                    continue
                hosts.append(host)
    return hosts


def duplicate_hosts(hosts: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for host in hosts:
        if host in seen and host not in dupes:
            dupes.append(host)
        seen.add(host)
    return dupes


def find_duplicate_host_rules(paths: list[Path]) -> list[str]:
    return duplicate_hosts(collect_host_rules(paths))


def compose_host_globs(repo_root: Path) -> list[Path]:
    app = repo_root / "app"
    paths = [app / "docker-compose.yml"]
    sites = app / "sites"
    if sites.is_dir():
        for compose in sorted(sites.glob("*/docker-compose.yml")):
            # Skip template dirs (_example, _example_php, …)
            if compose.parent.name.startswith("_"):
                continue
            paths.append(compose)
    return [p for p in paths if p.is_file()]
