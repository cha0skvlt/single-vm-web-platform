from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from scripts.inventory import validate_inventory


@pytest.mark.unit
def test_fail2ban_jail_template():
    env = Environment(
        loader=FileSystemLoader(
            Path(__file__).resolve().parents[2] / "ansible/roles/fail2ban/templates"
        ),
        autoescape=False,
    )
    out = env.get_template("jail.local.j2").render(
        ssh_port=666,
        fail2ban_ignoreip=["127.0.0.1/8", "::1"],
        fail2ban_traefik_auth_enabled=True,
        fail2ban_traefik_auth_maxretry=5,
        fail2ban_traefik_auth_findtime=600,
        fail2ban_traefik_auth_bantime=3600,
        fail2ban_traefik_auth_logpath="/var/log/prod/traefik/access.log",
        fail2ban_traefik_scan_enabled=True,
        fail2ban_traefik_scan_maxretry=5,
        fail2ban_traefik_scan_findtime=600,
        fail2ban_traefik_scan_bantime=3600,
        fail2ban_traefik_scan_logpath="/var/log/prod/traefik/access.log",
    )
    assert "port = 666" in out or "port = {{ ssh_port }}" not in out
    assert "[traefik-auth]" in out
    assert "[traefik-scan]" in out
    assert "filter = traefik-auth" in out
    assert "filter = traefik-scan" in out
    assert "banaction = ufw" in out
    assert "ignoreip = 127.0.0.1/8 ::1" in out


@pytest.mark.unit
def test_fail2ban_jail_template_traefik_auth_disabled():
    env = Environment(
        loader=FileSystemLoader(
            Path(__file__).resolve().parents[2] / "ansible/roles/fail2ban/templates"
        ),
        autoescape=False,
    )
    out = env.get_template("jail.local.j2").render(
        ssh_port=666,
        fail2ban_ignoreip=["127.0.0.1/8", "::1"],
        fail2ban_traefik_auth_enabled=False,
        fail2ban_traefik_auth_maxretry=5,
        fail2ban_traefik_auth_findtime=600,
        fail2ban_traefik_auth_bantime=3600,
        fail2ban_traefik_auth_logpath="/var/log/prod/traefik/access.log",
        fail2ban_traefik_scan_enabled=False,
        fail2ban_traefik_scan_maxretry=5,
        fail2ban_traefik_scan_findtime=600,
        fail2ban_traefik_scan_bantime=3600,
        fail2ban_traefik_scan_logpath="/var/log/prod/traefik/access.log",
    )
    assert "[sshd]" in out
    assert "[traefik-auth]" not in out
    assert "[traefik-scan]" not in out
