"""TLS staging paths, PEM classification, and per-domain Traefik dynamic TLS configs."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

TRAEFIK_DYNAMIC_CERTS = "/etc/traefik/dynamic/certs"
LEGACY_DOMAINS_AGG = "certs.domains.yml"

PEM_CERT_BEGIN = "-----BEGIN CERTIFICATE-----"
PEM_CERT_END = "-----END CERTIFICATE-----"
STAGING_SEARCH_DIRS = ("ssl", "certs-staging")
CERT_SUFFIXES = (".pem", ".crt", ".cer")
class CertRole(str, Enum):
    ROOT = "root"
    LEAF = "leaf"
    INTERMEDIATE = "intermediate"


class StagingError(Exception):
    """Invalid or incomplete TLS staging material."""


@dataclass(frozen=True)
class ClassifiedCert:
    pem: str
    role: CertRole
    subject: str
    issuer: str


@dataclass(frozen=True)
class StagingBuildResult:
    domain: str
    staging_dir: Path
    leaf_subject: str
    leaf_issuer: str
    san_ext: str
    not_after: str
    pubkey_sha256: str
    verify_ok: bool
    verify_message: str
    intermediate_subjects: tuple[str, ...]


def iter_cert_domains(certs_root: Path) -> list[str]:
    if not certs_root.is_dir():
        return []
    domains: list[str] = []
    for entry in sorted(certs_root.iterdir()):
        if not entry.is_dir() or entry.name == "_default":
            continue
        if (entry / "fullchain.pem").is_file() and (entry / "privkey.pem").is_file():
            domains.append(entry.name)
    return domains


def _cert_paths(domain: str) -> tuple[str, str]:
    base = f"{TRAEFIK_DYNAMIC_CERTS}/{domain}"
    return f"{base}/fullchain.pem", f"{base}/privkey.pem"


def render_domain_yml(domain: str, *, header: str | None = None) -> str:
    cert_file, key_file = _cert_paths(domain)
    lines: list[str] = []
    if header is not None:
        lines.append(header)
    else:
        lines.append(f"# TLS for {domain}")
    lines.extend(
        [
            "tls:",
            "  certificates:",
            f"    - certFile: {cert_file}",
            f"      keyFile: {key_file}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_default_store_yml(domain: str) -> str:
    """Use purchased/wildcard cert as Traefik default store (covers all routers on this edge)."""
    cert_file, key_file = _cert_paths(domain)
    return (
        "# Wildcard default TLS store\n"
        "tls:\n"
        "  stores:\n"
        "    default:\n"
        "      defaultCertificate:\n"
        f"        certFile: {cert_file}\n"
        f"        keyFile: {key_file}\n"
    )


def validate_yaml_file(path: Path) -> None:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"invalid YAML root in {path}")


def atomic_write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        validate_yaml_file(tmp)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def write_default_store_yml(domain: str, output: Path) -> None:
    atomic_write_file(output, render_default_store_yml(domain))


def write_domain_yml(domain: str, dynamic_dir: Path, *, header: str | None = None) -> Path:
    out = dynamic_dir / f"{domain}.yml"
    atomic_write_file(out, render_domain_yml(domain, header=header))
    return out


def remove_legacy_domains_agg(dynamic_dir: Path) -> None:
    legacy = dynamic_dir / LEGACY_DOMAINS_AGG
    if legacy.is_file():
        legacy.unlink()


def write_all_domain_ymls(certs_root: Path, dynamic_dir: Path) -> list[Path]:
    written = [write_domain_yml(domain, dynamic_dir) for domain in iter_cert_domains(certs_root)]
    remove_legacy_domains_agg(dynamic_dir)
    return written


def staging_dir(repo_root: Path, domain: str) -> Path | None:
    for name in STAGING_SEARCH_DIRS:
        candidate = repo_root / name / domain
        if candidate.is_dir():
            return candidate
    return None


def missing_staging_files(repo_root: Path, domain: str) -> list[Path]:
    base = staging_dir(repo_root, domain)
    if base is None:
        return [repo_root / "ssl" / domain, repo_root / "certs-staging" / domain]
    missing: list[Path] = []
    if not _staging_has_key(base):
        missing.append(base / "(private key)")
    if not _staging_has_cert_material(base):
        missing.append(base / "(certificate PEM)")
    return missing


def _staging_has_key(staging: Path) -> bool:
    for path in sorted(staging.iterdir()):
        if path.is_file() and _file_is_private_key(path):
            return True
    return False


def _staging_has_cert_material(staging: Path) -> bool:
    for path in sorted(staging.iterdir()):
        if path.is_file() and path.suffix.lower() in CERT_SUFFIXES:
            if path.name == "privkey.pem":
                continue
            if split_pem_certs(path.read_text(encoding="utf-8", errors="replace")):
                return True
    return False


def split_pem_certs(text: str) -> list[str]:
    blocks: list[str] = []
    start = 0
    while True:
        begin = text.find(PEM_CERT_BEGIN, start)
        if begin < 0:
            break
        end = text.find(PEM_CERT_END, begin)
        if end < 0:
            break
        block = text[begin : end + len(PEM_CERT_END)]
        if block not in blocks:
            blocks.append(block)
        start = end + len(PEM_CERT_END)
    return blocks


def _run_openssl(args: list[str], *, input_text: str | None = None) -> str:
    proc = subprocess.run(
        ["openssl", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise StagingError(detail or f"openssl {' '.join(args)} failed")
    return proc.stdout


def _file_is_private_key(path: Path) -> bool:
    proc = subprocess.run(
        ["openssl", "pkey", "-in", str(path), "-noout"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _x509_field(pem: str, flag: str) -> str:
    return _run_openssl(["x509", "-noout", flag], input_text=pem).strip()


def _x509_extensions(pem: str) -> str:
    try:
        return _run_openssl(["x509", "-noout", "-text"], input_text=pem)
    except StagingError:
        return ""


def _dn_value(dn_line: str) -> str:
    line = dn_line.strip()
    for prefix in ("subject=", "issuer="):
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return line


def _normalize_dn(dn: str) -> str:
    return re.sub(r"\s+", "", _dn_value(dn))


def _is_self_signed(subject: str, issuer: str) -> bool:
    return _normalize_dn(subject) == _normalize_dn(issuer)


def _basic_constraints_ca_false(text: str) -> bool:
    return bool(re.search(r"CA:\s*FALSE", text, re.IGNORECASE))


def _basic_constraints_ca_true(text: str) -> bool:
    return bool(re.search(r"CA:\s*TRUE", text, re.IGNORECASE))


def _has_subject_alt_name(text: str) -> bool:
    return "Subject Alternative Name" in text


def _san_summary(leaf_pem: str) -> str:
    text = _x509_extensions(leaf_pem)
    for line in text.splitlines():
        if "Subject Alternative Name" in line:
            return line.strip()
    return "Subject Alternative Name: (none)"


def classify_cert_pem(pem: str) -> ClassifiedCert:
    subject = _x509_field(pem, "-subject")
    issuer = _x509_field(pem, "-issuer")
    text = _x509_extensions(pem)
    if _is_self_signed(subject, issuer):
        role = CertRole.ROOT
    elif _basic_constraints_ca_true(text):
        role = CertRole.INTERMEDIATE
    elif _basic_constraints_ca_false(text) or _has_subject_alt_name(text):
        role = CertRole.LEAF
    else:
        role = CertRole.LEAF
    return ClassifiedCert(pem=pem, role=role, subject=subject, issuer=issuer)


def pubkey_sha256_from_pem(pem: str, *, cert: bool) -> str:
    if cert:
        pub = _run_openssl(["x509", "-noout", "-pubkey"], input_text=pem)
    else:
        pub = _run_openssl(["pkey", "-pubout"], input_text=pem)
    digest = _run_openssl(["sha256"], input_text=pub).strip()
    return digest.split()[-1] if digest else digest


def pubkey_sha256_from_key_file(path: Path) -> str:
    pub = subprocess.check_output(
        ["openssl", "pkey", "-in", str(path), "-pubout"],
        text=True,
    )
    digest = subprocess.check_output(
        ["openssl", "sha256"],
        input=pub,
        text=True,
    ).strip()
    return digest.split()[-1]


def _collect_private_key(staging: Path) -> Path:
    keys = [p for p in sorted(staging.iterdir()) if p.is_file() and _file_is_private_key(p)]
    if not keys:
        raise StagingError(f"no private key in {staging}")
    if len(keys) > 1:
        names = ", ".join(k.name for k in keys)
        raise StagingError(f"multiple private keys in {staging}: {names}")
    return keys[0]


def _collect_classified_certs(staging: Path) -> list[ClassifiedCert]:
    classified: list[ClassifiedCert] = []
    seen: set[str] = set()
    for path in sorted(staging.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in CERT_SUFFIXES:
            continue
        if path.name == "privkey.pem":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in split_pem_certs(text):
            if block in seen:
                continue
            seen.add(block)
            classified.append(classify_cert_pem(block))
    if not classified:
        raise StagingError(f"no certificates in {staging}")
    return classified


def _find_leaf_for_key(
    classified: list[ClassifiedCert], key_path: Path
) -> ClassifiedCert:
    key_fp = pubkey_sha256_from_key_file(key_path)
    matches = [
        c
        for c in classified
        if pubkey_sha256_from_pem(c.pem, cert=True) == key_fp
    ]
    if not matches:
        raise StagingError(
            "HARD GATE: public key does not match any certificate (abort before deploy)"
        )
    if len(matches) > 1:
        raise StagingError("HARD GATE: multiple certificates match the private key")
    return matches[0]


def _assert_leaf_not_expired(leaf: ClassifiedCert) -> None:
    proc = subprocess.run(
        ["openssl", "x509", "-noout", "-checkend", "0"],
        input=leaf.pem,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise StagingError("HARD GATE: leaf certificate has expired (abort before deploy)")


def _ordered_intermediates(
    leaf: ClassifiedCert, pool: list[ClassifiedCert]
) -> list[ClassifiedCert]:
    by_subject = {
        _normalize_dn(c.subject): c for c in pool if c.pem != leaf.pem
    }
    ordered: list[ClassifiedCert] = []
    want = _normalize_dn(leaf.issuer)
    seen_subjects: set[str] = set()
    while want and want not in seen_subjects:
        seen_subjects.add(want)
        nxt = by_subject.get(want)
        if nxt is None or nxt.role == CertRole.ROOT:
            break
        ordered.append(nxt)
        want = _normalize_dn(nxt.issuer)
    return ordered


def _build_fullchain_pem(leaf: ClassifiedCert, intermediates: list[ClassifiedCert]) -> str:
    chain = [
        c for c in intermediates if c.role != CertRole.ROOT and c.pem != leaf.pem
    ]
    parts = [leaf.pem, *[c.pem for c in chain]]
    return "\n".join(p.strip() for p in parts) + "\n"


def _verify_chain(leaf: ClassifiedCert, intermediates: list[ClassifiedCert]) -> tuple[bool, str]:
    if not intermediates:
        return False, "no intermediate certificates for verify"
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        leaf_path = tdir / "leaf.pem"
        untrusted = tdir / "untrusted.pem"
        leaf_path.write_text(leaf.pem, encoding="utf-8")
        untrusted.write_text("".join(c.pem for c in intermediates), encoding="utf-8")
        proc = subprocess.run(
            ["openssl", "verify", "-untrusted", str(untrusted), str(leaf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        msg = (proc.stdout or proc.stderr or "").strip()
        return proc.returncode == 0, msg


def materialize_staging(
    repo_root: Path,
    domain: str,
    *,
    certs_root: Path | None = None,
) -> StagingBuildResult:
    staging = staging_dir(repo_root, domain)
    if staging is None:
        raise StagingError(f"staging directory not found for {domain} (ssl/ or certs-staging/)")

    key_path = _collect_private_key(staging)
    classified = _collect_classified_certs(staging)
    leaf = _find_leaf_for_key(classified, key_path)
    _assert_leaf_not_expired(leaf)

    pool = [c for c in classified if c.pem != leaf.pem]
    intermediates = _ordered_intermediates(leaf, pool)
    if not intermediates:
        intermediates = [c for c in pool if c.role == CertRole.INTERMEDIATE]
    fullchain = _build_fullchain_pem(leaf, intermediates)

    out_root = certs_root or (repo_root / "app" / "certs")
    out_dir = out_root / domain
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fullchain.pem").write_text(fullchain, encoding="utf-8")
    key_pem = key_path.read_text(encoding="utf-8")
    priv = out_dir / "privkey.pem"
    priv.write_text(key_pem, encoding="utf-8")
    priv.chmod(0o600)

    verify_ok, verify_message = _verify_chain(leaf, intermediates)
    key_fp = pubkey_sha256_from_key_file(key_path)
    return StagingBuildResult(
        domain=domain,
        staging_dir=staging,
        leaf_subject=leaf.subject,
        leaf_issuer=leaf.issuer,
        san_ext=_san_summary(leaf.pem),
        not_after=_x509_field(leaf.pem, "-enddate"),
        pubkey_sha256=key_fp,
        verify_ok=verify_ok,
        verify_message=verify_message,
        intermediate_subjects=tuple(c.subject for c in intermediates),
    )

