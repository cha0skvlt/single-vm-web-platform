"""Traefik middleware Jinja2 render contract."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.mark.service
def test_traefik_middlewares_file_provider(repo_root: Path):
    from jinja2 import Environment, FileSystemLoader

    compose = (repo_root / "app/docker-compose.yml").read_text(encoding="utf-8")
    assert "traefik.http.middlewares.prod-headers" not in compose
    assert "prod-headers@file" in compose
    env = Environment(
        loader=FileSystemLoader(repo_root / "ansible/roles/app/templates"),
        autoescape=False,
    )
    rendered = env.get_template("middlewares.yml.j2").render(
        prod_public_host="status.example.com",
        prod_admin_user="admin",
    )
    mw = yaml.safe_load(rendered)
    names = set(mw["http"]["middlewares"].keys())
    assert names == {
        "prod-session-forward",
        "grafana-proxy-user",
        "prod-headers",
        "strip-traefik-prefix",
        "strip-pma-prefix",
    }
    forward = mw["http"]["middlewares"]["prod-session-forward"]["forwardAuth"]
    assert forward["maxResponseBodySize"] == 8192
    assert forward["maxBodySize"] == 8192
    assert forward["trustForwardHeader"] is False
    grafana_user = mw["http"]["middlewares"]["grafana-proxy-user"]["headers"]
    assert grafana_user["customRequestHeaders"]["X-WEBAUTH-USER"] == "panel"
    prod_headers = mw["http"]["middlewares"]["prod-headers"]["headers"]
    assert prod_headers["frameDeny"] is True
    assert prod_headers["contentTypeNosniff"] is True
    assert prod_headers["stsSeconds"] == 31536000
    assert "contentSecurityPolicy" not in prod_headers
    assert "prod-panel-csp" not in names
    panel_compose = (repo_root / "app/sites/ops-panel/docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "prod-headers@file" in panel_compose
    assert "prod-panel-csp" not in panel_compose
