from __future__ import annotations

from pathlib import Path

import pytest

from scripts.traefik import (
    collect_host_rules,
    duplicate_hosts,
    find_duplicate_host_rules,
    parse_host_rule,
    resolve_prod_public_host,
)


@pytest.mark.unit
def test_parse_host_rule():
    assert parse_host_rule("Host(`status.example.com`)") == "status.example.com"
    assert parse_host_rule("Host(`${PROD_PUBLIC_HOST}`)") is None
    assert parse_host_rule("") is None
    assert parse_host_rule("PathPrefix(`/api`)") is None


@pytest.mark.unit
def test_resolve_prod_public_host_prefers_server_env():
    host, warnings = resolve_prod_public_host(
        remote_env="PROD_PUBLIC_HOST=status.example.com\n",
        traefik_rule="Host(`prod.example`)",
        local="prod.example",
    )
    assert host == "status.example.com"
    assert any("local" in w for w in warnings)


@pytest.mark.unit
def test_resolve_prod_public_host_falls_back_to_traefik_label():
    host, warnings = resolve_prod_public_host(
        remote_env="LE_EMAIL=x@y.z\n",
        traefik_rule="Host(`status.example.com`)",
        local="prod.example",
    )
    assert host == "status.example.com"
    assert any("Traefik label" in w for w in warnings)


@pytest.mark.unit
def test_resolve_prod_public_host_falls_back_to_local():
    host, warnings = resolve_prod_public_host(
        remote_env="",
        traefik_rule="",
        local="prod.example",
    )
    assert host == "prod.example"
    assert any("using local" in w for w in warnings)


@pytest.mark.unit
def test_resolve_prod_public_host_falls_back_to_default():
    host, warnings = resolve_prod_public_host()
    assert host == "status.example.com"
    assert any("default" in w for w in warnings)


@pytest.mark.unit
def test_duplicate_hosts_empty():
    assert duplicate_hosts([]) == []


@pytest.mark.unit
def test_duplicate_hosts_detects_dup():
    assert duplicate_hosts(["a.local", "b.local", "a.local"]) == ["a.local"]


@pytest.mark.unit
def test_collect_host_rules_skips_env_placeholders(tmp_path: Path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "labels:\n"
        "  - traefik.http.routers.a.rule=Host(`${PROD_PUBLIC_HOST}`)\n"
        "  - traefik.http.routers.b.rule=Host(`only.local`)\n",
        encoding="utf-8",
    )
    assert collect_host_rules([compose]) == ["only.local"]


@pytest.mark.unit
def test_collect_host_rules_skips_comments(tmp_path: Path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "labels:\n"
        "  - traefik.http.routers.a.rule=Host(`active.local`)\n"
        "  # - traefik.http.routers.b.rule=Host(`commented.local`)\n",
        encoding="utf-8",
    )
    assert collect_host_rules([compose]) == ["active.local"]


@pytest.mark.unit
def test_collect_host_rules_from_compose(tmp_path: Path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        'labels:\n  - traefik.http.routers.x.rule=Host(`foo.local`)\n',
        encoding="utf-8",
    )
    assert collect_host_rules([compose]) == ["foo.local"]
