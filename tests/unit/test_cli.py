from __future__ import annotations

from pathlib import Path

import pytest

from scripts.cli import main


@pytest.mark.unit
def test_cli_check_traefik_ok():
    assert main(["check-traefik-hosts"]) == 0


@pytest.mark.unit
def test_cli_check_traefik_dupes(tmp_path: Path, monkeypatch, capsys):
    app = tmp_path / "app"
    sites = app / "sites" / "a"
    sites.mkdir(parents=True)
    dup = 'labels:\n  - traefik.http.routers.x.rule=Host(`dup.local`)\n'
    (app / "docker-compose.yml").write_text(dup, encoding="utf-8")
    (sites / "docker-compose.yml").write_text(dup, encoding="utf-8")
    monkeypatch.setattr("scripts.cli._repo_root", lambda: tmp_path)
    assert main(["check-traefik-hosts"]) == 1
    assert "dup.local" in capsys.readouterr().err


@pytest.mark.unit
def test_cli_check_staging_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.cli._repo_root", lambda: tmp_path)
    assert main(["check-staging", "missing.example"]) == 1


@pytest.mark.unit
def test_cli_check_staging_ok(tmp_path, monkeypatch):
    import subprocess

    stage = tmp_path / "ssl" / "ok.ru"
    stage.mkdir(parents=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(stage / "privkey.pem"),
            "-out",
            str(stage / "cert.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=ok.ru",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr("scripts.cli._repo_root", lambda: tmp_path)
    assert main(["check-staging", "ok.ru"]) == 0


@pytest.mark.unit
def test_cli_materialize_staging_error(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.cli._repo_root", lambda: tmp_path)
    assert main(["materialize-staging", "nope.test"]) == 1


@pytest.mark.unit
@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "ssl/example.com").is_dir(),
    reason="ssl/example.com not present",
)
def test_cli_materialize_example(repo_root: Path, monkeypatch):
    monkeypatch.setattr("scripts.cli._repo_root", lambda: repo_root)
    assert main(["materialize-staging", "example.com"]) == 0


@pytest.mark.unit
def test_cli_materialize_staging(tmp_path, monkeypatch):
    import subprocess

    stage = tmp_path / "ssl" / "m.ru"
    stage.mkdir(parents=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(stage / "key.pem"),
            "-out",
            str(stage / "cert.pem"),
            "-days",
            "5",
            "-subj",
            "/CN=m.ru",
            "-addext",
            "subjectAltName=DNS:m.ru",
            "-addext",
            "basicConstraints=CA:FALSE",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr("scripts.cli._repo_root", lambda: tmp_path)
    assert main(["materialize-staging", "m.ru"]) == 0
    assert (tmp_path / "app/certs/m.ru/fullchain.pem").is_file()

