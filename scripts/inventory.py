"""Ansible inventory.ini validation."""

from __future__ import annotations

import os
import re
from pathlib import Path

from scripts.project import ENV_KEY

# Distinct from inventory group (PROJECT_HOSTNAME); playbook uses hosts: all.
INVENTORY_HOST_ALIAS = "vm"

_IPV4_RE = re.compile(r"^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$")


def is_ipv4_address(value: str) -> bool:
    text = value.strip()
    if not _IPV4_RE.match(text):
        return False
    return all(0 <= int(part) <= 255 for part in text.split("."))


def host_from_known_hosts_field(first_field: str) -> str | None:
    """Extract IPv4 from ssh-keyscan first column ([ip]:port, ip:port, or ip)."""
    field = first_field.strip()
    if not field or field.startswith("#"):
        return None
    if field.startswith("[") and "]" in field:
        inner = field[1 : field.index("]")]
        if ":" in inner:
            inner = inner.split(":", 1)[0]
        return inner if is_ipv4_address(inner) else None
    host = field.split(":", 1)[0]
    return host if is_ipv4_address(host) else None


def parse_deploy_host(*, candidate: str | None, known_hosts: str) -> str:
    """Resolve prod IPv4 from DEPLOY_HOST and/or DEPLOY_KNOWN_HOSTS (ssh-keyscan lines)."""
    if candidate:
        host = candidate.strip()
        if host.startswith("#"):
            host = ""
        if is_ipv4_address(host):
            return host
    seen: list[str] = []
    for line in known_hosts.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        host = host_from_known_hosts_field(parts[0])
        if host and host not in seen:
            seen.append(host)
    if not seen:
        raise ValueError(
            "could not determine deploy host (set DEPLOY_HOST to IPv4 or fix DEPLOY_KNOWN_HOSTS)"
        )
    if len(seen) > 1:
        raise ValueError(
            f"DEPLOY_KNOWN_HOSTS lists multiple IPv4 addresses: {', '.join(seen)}"
        )
    return seen[0]


def inventory_host_alias(text: str) -> str:
    """Return inventory host alias from the line containing ansible_host=."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") or "ansible_host=" not in stripped:
            continue
        alias = stripped.split()[0]
        if alias:
            return alias
    raise ValueError("inventory host alias not found")


def ensure_inventory_hostname(text: str, *, expected: str) -> None:
    group = inventory_group_name(text)
    if group != expected:
        raise ValueError(
            f"inventory group [{group}] does not match PROJECT_HOSTNAME {expected!r}"
        )


def filter_known_hosts_for_ssh(text: str) -> str:
    """Drop blank/comment lines; keep ssh-keyscan host key lines only."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if parts and host_from_known_hosts_field(parts[0]):
            kept.append(line.rstrip("\r"))
    return "\n".join(kept) + ("\n" if kept else "")


def _expected_hostname() -> str:
    value = os.environ.get(ENV_KEY, "").strip()
    if not value:
        raise RuntimeError(f"{ENV_KEY} is not set")
    return value


def validate_inventory(text: str, *, hostname: str | None = None) -> list[str]:
    errors: list[str] = []
    try:
        host = hostname or _expected_hostname()
    except RuntimeError as exc:
        return [str(exc)]
    if f"[{host}]" not in text:
        errors.append(f"missing [{host}] group")
    for key in ("ansible_host=", "ansible_user=", "ansible_port="):
        if key not in text:
            errors.append(f"missing {key}")
    return errors


def materialize_inventory_example(text: str, *, hostname: str | None = None) -> str:
    """Replace HOST placeholders in inventory.ini.example."""
    host = hostname or _expected_hostname()
    return text.replace("HOST", host)


def parse_inventory_connection(text: str) -> tuple[str, int, str]:
    """Return (ansible_host, ansible_port, ansible_user) from inventory.ini text."""
    host = ""
    port = 22
    user = "deploy"
    for line in text.splitlines():
        if "ansible_host=" in line:
            host = line.split("ansible_host=", 1)[1].split()[0].strip()
        if "ansible_port=" in line:
            port = int(line.split("ansible_port=", 1)[1].split()[0].strip())
        if "ansible_user=" in line:
            user = line.split("ansible_user=", 1)[1].split()[0].strip()
    if not host:
        raise ValueError("ansible_host not found in inventory")
    return host, port, user


def inventory_group_name(text: str) -> str:
    """Return the first group name (section header) from inventory text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            if name:
                return name
    raise ValueError("inventory group name not found")


def render_inventory_ini(*, hostname: str, ansible_host: str, ansible_port: int = 22) -> str:
    host = hostname.strip()
    addr = ansible_host.strip()
    if not host:
        raise ValueError("hostname must be non-empty")
    if not addr:
        raise ValueError("ansible_host must be non-empty")
    return (
        f"[{host}]\n"
        f"{INVENTORY_HOST_ALIAS} ansible_host={addr} ansible_user=deploy "
        f"ansible_port={ansible_port}\n"
    )


def sync_inventory_group(text: str, *, expected: str) -> tuple[str, bool]:
    """Align inventory group with PROJECT_HOSTNAME; keep ansible_host/port."""
    target = expected.strip()
    if not target:
        raise ValueError("expected hostname must be non-empty")
    group = inventory_group_name(text)
    if group == target:
        return text, False
    addr, port, _user = parse_inventory_connection(text)
    return render_inventory_ini(hostname=target, ansible_host=addr, ansible_port=port), True


def prepare_ansible_inventory(path: Path, *, expected: str) -> tuple[str, int, str, str | None]:
    """Sync inventory group to PROJECT_HOSTNAME; return SSH connection tuple and optional status."""
    inv_path = Path(path)
    text = inv_path.read_text(encoding="utf-8")
    old_group = inventory_group_name(text)
    new_text, changed = sync_inventory_group(text, expected=expected)
    message = None
    if changed:
        inv_path.write_text(new_text, encoding="utf-8")
        message = f"inventory: fixed group [{old_group}] -> [{expected}]"
        text = new_text
    host, port, user = parse_inventory_connection(text)
    return host, port, user, message
