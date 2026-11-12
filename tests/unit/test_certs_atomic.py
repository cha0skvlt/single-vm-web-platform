from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.certs import (
    LEGACY_DOMAINS_AGG,
    atomic_write_file,
    render_domain_yml,
    validate_yaml_file,
    write_domain_yml,
)


@pytest.mark.unit
def test_render_domain_yml_paths():
    text = render_domain_yml("example.com")
    assert "/etc/traefik/dynamic/certs/example.com/fullchain.pem" in text
    assert "tls:" in text
    yaml.safe_load(text)


@pytest.mark.unit
def test_atomic_write_replaces_valid(tmp_path: Path):
    out = tmp_path / "example.com.yml"
    atomic_write_file(out, render_domain_yml("example.com"))
    assert out.is_file()
    assert not (tmp_path / "example.com.yml.tmp").exists()
    first = out.read_text(encoding="utf-8")
    atomic_write_file(out, render_domain_yml("example.com", header="# updated"))
    assert "updated" in out.read_text(encoding="utf-8")
    assert first != out.read_text(encoding="utf-8")


@pytest.mark.unit
def test_atomic_write_keeps_old_file_on_invalid_yaml(tmp_path: Path):
    out = tmp_path / "bad.ru.yml"
    atomic_write_file(out, render_domain_yml("bad.ru"))
    good = out.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML root"):
        atomic_write_file(out, "- only\n- list\n")
    assert out.read_text(encoding="utf-8") == good
    assert not (tmp_path / "bad.ru.yml.tmp").exists()


@pytest.mark.unit
def test_write_all_domain_ymls_removes_legacy_agg(tmp_path: Path):
    from scripts.certs import write_all_domain_ymls

    certs = tmp_path / "certs"
    site = certs / "site.ru"
    site.mkdir(parents=True)
    (site / "fullchain.pem").write_text("c", encoding="utf-8")
    (site / "privkey.pem").write_text("k", encoding="utf-8")
    dynamic = tmp_path / "dynamic"
    dynamic.mkdir()
    legacy = dynamic / LEGACY_DOMAINS_AGG
    legacy.write_text("tls: {}\n", encoding="utf-8")
    write_all_domain_ymls(certs, dynamic)
    assert (dynamic / "site.ru.yml").is_file()
    assert not legacy.exists()


@pytest.mark.unit
def test_validate_yaml_file_rejects_non_mapping(tmp_path: Path):
    path = tmp_path / "x.yml"
    path.write_text("- only\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML root"):
        validate_yaml_file(path)
