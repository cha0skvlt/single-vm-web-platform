"""Prometheus scrape layout — shared vs per-site exporters."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.mark.unit
def test_prometheus_scrape_jobs(repo_root: Path):
    path = repo_root / "app/prometheus/prometheus.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = {item["job_name"]: item for item in doc["scrape_configs"]}

    mysql = jobs["mysqld-exporter"]
    assert mysql["static_configs"][0]["targets"] == ["mysqld-exporter:9104"]
    assert mysql["static_configs"][0]["labels"]["site"] == "shared"

    site = jobs["site-exporters"]
    assert site["docker_sd_configs"][0]["match_first_network"] is False
    relabel = site["relabel_configs"]
    assert any(
        r.get("source_labels") == ["__meta_docker_network_name"]
        and r.get("regex") == "^web$"
        and r.get("action") == "keep"
        for r in relabel
    )
    assert "docker_sd_configs" in site


@pytest.mark.unit
def test_app_handlers_restart_prometheus_and_grafana(repo_root: Path):
    handlers = (repo_root / "ansible/roles/app/handlers/main.yml").read_text(encoding="utf-8")
    assert "restart prometheus" in handlers
    assert "restart grafana" in handlers
    assert "grafana-prune-stale.sh" not in handlers
    assert not (repo_root / "scripts/grafana-prune-stale.sh").exists()
    logs = (repo_root / "app/grafana/provisioning/dashboards/json/platform/logs.json").read_text(
        encoding="utf-8"
    )
    assert '{host=\\"web\\"' in logs
    assert "$search" in logs
