from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.certs import (
    CertRole,
    StagingError,
    classify_cert_pem,
    iter_cert_domains,
    materialize_staging,
    missing_staging_files,
    render_default_store_yml,
    split_pem_certs,
    staging_dir,
    write_default_store_yml,
    write_all_domain_ymls,
)


@pytest.mark.unit
def test_iter_cert_domains_skips_default_and_incomplete(tmp_certs: Path):
    (tmp_certs / "_default").mkdir()
    (tmp_certs / "_default" / "fullchain.pem").write_text("x")
    complete = tmp_certs / "site.ru"
    complete.mkdir()
    (complete / "fullchain.pem").write_text("c")
    (complete / "privkey.pem").write_text("k")
    (tmp_certs / "broken").mkdir()
    (tmp_certs / "broken" / "fullchain.pem").write_text("c")
    assert iter_cert_domains(tmp_certs) == ["site.ru"]


@pytest.mark.unit
def test_render_default_store_yml(tmp_path: Path):
    text = render_default_store_yml("example.com")
    assert "/etc/traefik/dynamic/certs/example.com/fullchain.pem" in text
    out = tmp_path / "certs-default.yml"
    write_default_store_yml("example.com", out)
    assert out.read_text(encoding="utf-8") == text


@pytest.mark.unit
def test_write_domains_yml(tmp_certs: Path, tmp_path: Path):
    from scripts.certs import write_all_domain_ymls

    d = tmp_certs / "x.local"
    d.mkdir()
    (d / "fullchain.pem").write_text("c")
    (d / "privkey.pem").write_text("k")
    dynamic = tmp_path / "dynamic"
    paths = write_all_domain_ymls(tmp_certs, dynamic)
    assert len(paths) == 1
    assert "x.local" in paths[0].read_text(encoding="utf-8")


@pytest.mark.unit
def test_missing_staging_files_no_dir(tmp_path: Path):
    assert len(missing_staging_files(tmp_path, "nope.com")) == 2


@pytest.mark.unit
def test_missing_staging_files_incomplete(tmp_path: Path):
    stage = tmp_path / "ssl" / "partial.ru"
    stage.mkdir(parents=True)
    assert missing_staging_files(tmp_path, "partial.ru")


@pytest.mark.unit
def test_staging_dir_prefers_ssl(tmp_path: Path):
    (tmp_path / "certs-staging" / "d.ru").mkdir(parents=True)
    ssl = tmp_path / "ssl" / "d.ru"
    ssl.mkdir(parents=True)
    assert staging_dir(tmp_path, "d.ru") == ssl


@pytest.mark.unit
def test_split_pem_certs_dedupes():
    block = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIB\n"
        "-----END CERTIFICATE-----\n"
    )
    text = block + block
    assert len(split_pem_certs(text)) == 1


@pytest.mark.unit
def test_classify_self_signed_root(tmp_path: Path):
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(tmp_path / "k.pem"),
            "-out",
            str(tmp_path / "r.pem"),
            "-days",
            "30",
            "-subj",
            "/CN=Test Root",
        ],
        check=True,
        capture_output=True,
    )
    pem = (tmp_path / "r.pem").read_text(encoding="utf-8")
    assert classify_cert_pem(pem).role == CertRole.ROOT


@pytest.mark.unit
def test_materialize_staging_success(tmp_path: Path):
    stage = tmp_path / "ssl" / "site.test"
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
            str(stage / "leaf.pem"),
            "-days",
            "30",
            "-subj",
            "/CN=site.test",
            "-addext",
            "subjectAltName=DNS:site.test",
            "-addext",
            "basicConstraints=CA:FALSE",
        ],
        check=True,
        capture_output=True,
    )
    result = materialize_staging(tmp_path, "site.test")
    out = tmp_path / "app/certs/site.test"
    assert (out / "fullchain.pem").is_file()
    assert (out / "privkey.pem").stat().st_mode & 0o777 == 0o600
    assert len(split_pem_certs((out / "fullchain.pem").read_text(encoding="utf-8"))) == 1


@pytest.mark.unit
def test_materialize_aborts_on_key_mismatch(tmp_path: Path):
    stage = tmp_path / "ssl" / "bad.test"
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
            str(stage / "unused.pem"),
            "-out",
            str(stage / "leaf.pem"),
            "-days",
            "30",
            "-subj",
            "/CN=bad.test",
        ],
        check=True,
        capture_output=True,
    )
    (stage / "unused.pem").unlink()
    subprocess.run(
        ["openssl", "genrsa", "-out", str(stage / "private.pem"), "2048"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(StagingError, match="HARD GATE"):
        materialize_staging(tmp_path, "bad.test")
    assert not (tmp_path / "app/certs/bad.test").exists()


@pytest.mark.unit
def test_split_pem_certs_stops_without_end_marker():
    text = "-----BEGIN CERTIFICATE-----\nZm9v\n"
    assert split_pem_certs(text) == []


@pytest.mark.unit
def test_classify_invalid_pem_raises():
    with pytest.raises(StagingError):
        classify_cert_pem("not a pem")


@pytest.mark.unit
def test_san_summary_without_san(tmp_path: Path):
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(tmp_path / "k.pem"),
            "-out",
            str(tmp_path / "c.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=nosan.test",
        ],
        check=True,
        capture_output=True,
    )
    from scripts.certs import _san_summary

    pem = (tmp_path / "c.pem").read_text(encoding="utf-8")
    assert _san_summary(pem) == "Subject Alternative Name: (none)"


@pytest.mark.unit
def test_assert_leaf_not_expired_gate(tmp_path: Path):
    from unittest.mock import patch

    from scripts.certs import ClassifiedCert, CertRole, _assert_leaf_not_expired

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(tmp_path / "k.pem"),
            "-out",
            str(tmp_path / "c.pem"),
            "-days",
            "5",
            "-subj",
            "/CN=old.test",
        ],
        check=True,
        capture_output=True,
    )
    pem = (tmp_path / "c.pem").read_text(encoding="utf-8")
    leaf = ClassifiedCert(
        pem=pem, role=CertRole.LEAF, subject="s", issuer="i"
    )
    with patch(
        "scripts.certs.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "", ""),
    ):
        with pytest.raises(StagingError, match="expired"):
            _assert_leaf_not_expired(leaf)


@pytest.mark.unit
def test_materialize_staging_dir_missing(tmp_path: Path):
    with pytest.raises(StagingError, match="staging directory not found"):
        materialize_staging(tmp_path, "missing.test")


@pytest.mark.unit
def test_staging_uses_certs_staging_fallback(tmp_path: Path):
    stage = tmp_path / "certs-staging" / "legacy.ru"
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
            "5",
            "-subj",
            "/CN=legacy.ru",
        ],
        check=True,
        capture_output=True,
    )
    assert staging_dir(tmp_path, "legacy.ru") == stage
    materialize_staging(tmp_path, "legacy.ru")


@pytest.mark.unit
def test_multiple_private_keys_error(tmp_path: Path):
    stage = tmp_path / "ssl" / "keys.ru"
    stage.mkdir(parents=True)
    for name in ("a.pem", "b.pem"):
        subprocess.run(
            ["openssl", "genrsa", "-out", str(stage / name), "2048"],
            check=True,
            capture_output=True,
        )
    from scripts.certs import _collect_private_key

    with pytest.raises(StagingError, match="multiple private keys"):
        _collect_private_key(stage)


@pytest.mark.unit
@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "ssl/example.com").is_dir(),
    reason="ssl/example.com not present",
)
def test_materialize_example_com(repo_root: Path):
    import yaml as pyyaml

    from scripts.cli import main

    assert main(["materialize-staging", "example.com"]) == 0
    chain = split_pem_certs(
        (repo_root / "app/certs/example.com/fullchain.pem").read_text(encoding="utf-8")
    )
    assert len(chain) == 2
    dynamic = repo_root / "app/traefik/dynamic"
    yml = dynamic / "example.com.yml"
    assert yml.is_file()
    assert not (dynamic / "certs.domains.yml").exists()
    pyyaml.safe_load(yml.read_text(encoding="utf-8"))
