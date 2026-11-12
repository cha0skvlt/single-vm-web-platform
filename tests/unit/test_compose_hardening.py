"""Wave-1 audit hardening: compose isolation and panel mounts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.mark.unit
def test_base_compose_audit_isolation(repo_root: Path):
    doc = yaml.safe_load((repo_root / "app/docker-compose.yml").read_text(encoding="utf-8"))
    services = doc["services"]

    traefik_nets = services["traefik"]["networks"]
    assert "web" in traefik_nets
    assert traefik_nets["platform"]["ipv4_address"] == "172.28.10.6"
    assert "db" not in traefik_nets
    assert "/var/run/docker.sock" not in "\n".join(services["traefik"].get("volumes") or [])
    assert "tcp://docker-socket-proxy:2375" in "\n".join(services["traefik"]["command"])
    proxy = services["docker-socket-proxy"]
    assert proxy["image"] == "tecnativa/docker-socket-proxy:0.3.0"
    assert proxy["networks"] == ["platform"]
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in proxy["volumes"]
    assert "/var/run/docker.sock" not in "\n".join(services["prometheus"].get("volumes") or [])
    assert "/var/run/docker.sock" not in "\n".join(services["alloy"].get("volumes") or [])

    grafana = services["grafana"]["environment"]
    assert grafana["GF_AUTH_PROXY_WHITELIST"] == "172.28.10.6/32"
    assert grafana["GF_AUTH_PROXY_DEFAULT_ROLE"] == "Viewer"
    assert services["alloy"]["networks"] == ["platform"]

    pma = services["phpmyadmin"]
    assert pma["image"] == "phpmyadmin:5.2.3"
    assert pma["environment"]["PMA_ARBITRARY"] == "0"
    assert "platform" in pma["networks"]
    assert "db" in pma["networks"]
    assert "web" not in pma["networks"]
    assert "traefik.docker.network=platform" in pma["labels"]

    assert services["node-exporter"]["networks"] == ["platform"]
    assert services["loki"]["image"] == "grafana/loki:3.7.4"
    assert services["mysql"]["image"] == "mysql:8.4.10"

    traefik_yml = yaml.safe_load(
        (repo_root / "app/traefik/traefik.yml").read_text(encoding="utf-8")
    )
    assert traefik_yml["entryPoints"]["traefik"]["address"] == "172.28.10.6:8080"
    assert traefik_yml["providers"]["docker"]["endpoint"] == "tcp://docker-socket-proxy:2375"

    prom = (repo_root / "app/prometheus/prometheus.yml").read_text(encoding="utf-8")
    assert "tcp://docker-socket-proxy:2375" in prom
    assert "unix:///var/run/docker.sock" not in prom
    alloy = (repo_root / "app/alloy/config.alloy").read_text(encoding="utf-8")
    assert "tcp://docker-socket-proxy:2375" in alloy
    assert "unix:///var/run/docker.sock" not in alloy


@pytest.mark.unit
def test_prod_status_mounts_exclude_env(repo_root: Path):
    doc = yaml.safe_load(
        (repo_root / "app/sites/ops-panel/docker-compose.yml").read_text(encoding="utf-8")
    )
    volumes = doc["services"]["ops-panel"]["volumes"]
    joined = "\n".join(volumes)
    assert "/opt/${PROJECT_HOSTNAME:-prod}/app/certs:" in joined
    assert "/opt/${PROJECT_HOSTNAME:-prod}/scripts:" in joined
    assert ":/opt/${PROJECT_HOSTNAME:-prod}:ro" not in joined


@pytest.mark.unit
def test_docker_user_isolation_unit(repo_root: Path):
    script = (
        repo_root / "ansible/roles/docker/files/docker-user-isolation.sh"
    ).read_text(encoding="utf-8")
    assert "DOCKER-USER-PLATFORM" in script
    assert "--dport 3100" in script
    unit = (
        repo_root / "ansible/roles/docker/templates/docker-user-isolation.service.j2"
    ).read_text(encoding="utf-8")
    assert "After=docker.service" in unit
    assert "PartOf=docker.service" in unit
    assert "WantedBy=multi-user.target docker.service" in unit
    assert "docker-user-isolation.sh" in unit
    assert "--dport 9323" in script
    assert "172.16.0.0/12" in script


@pytest.mark.unit
def test_smoke_prod_public_health_is_unauthenticated_redirect(repo_root: Path):
    smoke = (repo_root / "scripts/smoke-prod.sh").read_text(encoding="utf-8")
    assert 'expected HTTP 303 without session' in smoke
    assert "200 + ok" not in smoke
    assert "http://172.28.10.6:8080/api/http/routers" in smoke
