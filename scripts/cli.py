"""CLI entry points for CI and scripts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.certs import (
    StagingError,
    materialize_staging,
    missing_staging_files,
    remove_legacy_domains_agg,
    write_default_store_yml,
    write_domain_yml,
)
from scripts.sites import SiteError, prompt_site_add, site_add, site_rm
from scripts.traefik import compose_host_globs, find_duplicate_host_rules


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sync_app_env_to_github(repo_root: Path) -> None:
    """Copy local app/.env into GitHub APP_ENV (same as make gh-bootstrap)."""
    script = repo_root / "scripts/gh-bootstrap.sh"
    if not script.is_file():
        raise SiteError(f"missing {script.relative_to(repo_root)}")
    print("Syncing app/.env passwords to GitHub (APP_ENV)…")
    proc = subprocess.run([str(script)], cwd=repo_root, check=False)
    if proc.returncode != 0:
        raise SiteError(
            "failed to sync app/.env to GitHub (need: gh auth login, then retry site-add)"
        )


def cmd_check_traefik(_: argparse.Namespace) -> int:
    root = _repo_root()
    dupes = find_duplicate_host_rules(compose_host_globs(root))
    if dupes:
        print("Duplicate Traefik Host() rules:", ", ".join(dupes), file=sys.stderr)
        return 1
    return 0


def cmd_check_staging(args: argparse.Namespace) -> int:
    root = _repo_root()
    missing = missing_staging_files(root, args.domain)
    if missing:
        for path in missing:
            print(f"missing {path}", file=sys.stderr)
        return 1
    return 0


def cmd_site_add(args: argparse.Namespace) -> int:
    root = _repo_root()
    try:
        if getattr(args, "interactive", False) or not getattr(args, "domain", None):
            answers = prompt_site_add()
            result = site_add(
                root,
                str(answers["domain"]),
                php=bool(answers["php"]),
                php_version=str(answers.get("php_version") or "8.4"),
                node=bool(answers.get("node")),
                sql=bool(answers["sql"]),
                lets_encrypt=bool(answers["lets_encrypt"]),
                force=bool(getattr(args, "force", False)),
            )
        else:
            result = site_add(
                root,
                args.domain,
                force=args.force,
            )
    except (SiteError, EOFError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.get("env_secrets_written") == "True" or result.get("env_password_written") == "True":
        try:
            _sync_app_env_to_github(root)
        except SiteError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    for key, value in result.items():
        print(f"{key}: {value}")
    print("Done.")
    return 0


def cmd_site_rm(args: argparse.Namespace) -> int:
    root = _repo_root()
    try:
        result = site_rm(root, args.domain, force=args.force)
    except SiteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for key in (
        "domain",
        "site_dir",
        "env_example_removed",
        "env_test_example_removed",
    ):
        print(f"{key}: {result[key]}")
    return 0


def cmd_materialize_staging(args: argparse.Namespace) -> int:
    root = _repo_root()
    try:
        result = materialize_staging(root, args.domain)
    except StagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"staging: {result.staging_dir}")
    print(f"pubkey-sha256: {result.pubkey_sha256}")
    print(f"leaf: {result.leaf_subject}")
    print(f"issuer: {result.leaf_issuer}")
    for subj in result.intermediate_subjects:
        print(f"intermediate: {subj}")
    print(result.san_ext)
    print(result.not_after)
    if result.verify_ok:
        print(f"verify: OK ({result.verify_message})")
    else:
        print(f"WARN: chain verify failed: {result.verify_message}", file=sys.stderr)
    dynamic = root / "app/traefik/dynamic"
    domain_yml = write_domain_yml(args.domain, dynamic)
    default_yml = dynamic / "certs-default.yml"
    write_default_store_yml(args.domain, default_yml)
    remove_legacy_domains_agg(dynamic)
    print(f"wrote app/certs/{args.domain}/{{fullchain.pem,privkey.pem}}")
    print(f"wrote {domain_yml.relative_to(root)}")
    print(f"wrote {default_yml.relative_to(root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ops")
    sub = parser.add_subparsers(dest="command", required=True)

    p_traefik = sub.add_parser("check-traefik-hosts")
    p_traefik.set_defaults(func=cmd_check_traefik)

    p_staging = sub.add_parser("check-staging")
    p_staging.add_argument("domain")
    p_staging.set_defaults(func=cmd_check_staging)

    p_mat = sub.add_parser("materialize-staging")
    p_mat.add_argument("domain")
    p_mat.set_defaults(func=cmd_materialize_staging)

    p_add = sub.add_parser(
        "site-add",
        help="Interactive scaffold for app/sites/<domain> (static or PHP)",
    )
    p_add.add_argument(
        "domain",
        nargs="?",
        default=None,
        help="Optional FQDN for non-interactive static scaffold (tests)",
    )
    p_add.add_argument(
        "--force",
        action="store_true",
        help="Replace existing site dir",
    )
    p_add.add_argument(
        "--interactive",
        action="store_true",
        help="Ask domain/PHP/SQL/LE questions (default when domain omitted)",
    )
    p_add.set_defaults(func=cmd_site_add)

    p_rm = sub.add_parser("site-rm", help="Remove scaffolded site (OPS-2)")
    p_rm.add_argument("domain", help="Site FQDN to remove")
    p_rm.add_argument("--force", action="store_true", help="Required to delete site files")
    p_rm.set_defaults(func=cmd_site_rm)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
