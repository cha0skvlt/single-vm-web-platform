"""Site scaffold helpers (OPS-2): interactive site-add, templates, validation."""

from __future__ import annotations

import base64
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.envfile import parse_env_keys, read_env_value
from scripts.inventory import parse_inventory_connection
from scripts.project import install_root
from scripts.traefik import compose_host_globs, find_duplicate_host_rules

RESERVED_SITE_DIRS = frozenset(
    {"_example", "_example_php", "_example_php82", "_example_nuxt", "ops-panel"}
)
PHP_VERSIONS = frozenset({"8.2", "8.4"})
DEFAULT_PHP_VERSION = "8.4"

DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
DB_IDENT_RE = re.compile(r"^[a-z0-9_]{1,60}$")
CERTRESOLVER_LINE_RE = re.compile(
    r"^[ \t]*-[ \t]*traefik\.http\.routers\.[^\s]+\.tls\.certresolver=le[ \t]*\n",
    re.MULTILINE,
)


class SiteError(Exception):
    """Invalid site-add/site-rm input or conflicting repo state."""


def normalize_domain(domain: str) -> str:
    value = domain.strip().lower().rstrip(".")
    if not value or not DOMAIN_RE.fullmatch(value):
        raise SiteError(
            f"invalid domain {domain!r} (expected lowercase FQDN, e.g. example.ru)"
        )
    if value in RESERVED_SITE_DIRS:
        raise SiteError(f"domain {value!r} is reserved")
    return value


def domain_slug(domain: str) -> str:
    return normalize_domain(domain).replace(".", "-")


def db_ident_from_domain(domain: str) -> str:
    fqdn = normalize_domain(domain)
    ident = fqdn.replace(".", "_").replace("-", "_")
    if not DB_IDENT_RE.fullmatch(ident):
        raise SiteError(f"invalid db ident from domain {domain!r}")
    return ident


def db_env_key(ident: str) -> str:
    return f"{ident.upper()}_DB_PASSWORD"


def app_env_key(ident: str) -> str:
    return f"{ident.upper()}_APP_KEY"


def generate_app_key() -> str:
    return "base64:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def generate_db_password() -> str:
    """URL-safe random password for MySQL site users."""
    return secrets.token_urlsafe(32)


def resolve_db_password(env_file: Path, env_key: str, db_password: str = "") -> str:
    """Use explicit password, else reuse app/.env, else generate."""
    if db_password:
        return db_password
    if env_file.is_file():
        existing = read_env_value(env_file.read_text(encoding="utf-8"), env_key)
        if existing:
            return existing
    return generate_db_password()


def sql_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return "'" + escaped + "'"


def build_site_provision_sql(ident: str, password: str) -> str:
    user = sql_literal(ident)
    pw = sql_literal(password)
    return (
        f"CREATE DATABASE IF NOT EXISTS `{ident}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS {user}@'%' IDENTIFIED BY {pw};\n"
        f"GRANT ALL PRIVILEGES ON `{ident}`.* TO {user}@'%';\n"
        "FLUSH PRIVILEGES;\n"
    )


def build_exporter_provision_sql(password: str) -> str:
    pw = sql_literal(password)
    return (
        f"CREATE USER IF NOT EXISTS 'exporter'@'%' IDENTIFIED BY {pw};\n"
        "GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'exporter'@'%';\n"
        "GRANT SELECT ON performance_schema.* TO 'exporter'@'%';\n"
        "FLUSH PRIVILEGES;\n"
    )


def render_compose_text(
    template: str,
    *,
    domain: str,
    slug: str,
    db_ident: str,
    db_env_key_name: str,
    app_env_key_name: str = "EXAMPLE_APP_KEY",
) -> str:
    text = template.replace("example-internal", f"{slug}-internal")
    for suffix in ("web", "nginx-exporter", "php", "node"):
        text = text.replace(f"example-{suffix}", f"{slug}-{suffix}")
    text = text.replace("stack.site=example", f"stack.site={slug}")
    text = text.replace("traefik.http.routers.example", f"traefik.http.routers.{slug}")
    text = text.replace("traefik.http.services.example", f"traefik.http.services.{slug}")
    text = text.replace(".service=example", f".service={slug}")
    text = text.replace("_example.local", domain)
    text = text.replace("DB_DATABASE: example", f"DB_DATABASE: {db_ident}")
    text = text.replace("DB_USERNAME: example", f"DB_USERNAME: {db_ident}")
    text = text.replace("EXAMPLE_DB_PASSWORD", db_env_key_name)
    text = text.replace("EXAMPLE_APP_KEY", app_env_key_name)
    return text


def render_nginx_text(template: str, *, slug: str) -> str:
    return template.replace("example-php", f"{slug}-php")


def inventory_deploy_target(repo_root: Path) -> tuple[str | None, int]:
    """Return (host, port) from ansible/inventory.ini when the host is real."""
    inv = repo_root / "ansible" / "inventory.ini"
    if not inv.is_file():
        return None, 22
    try:
        host, port, _user = parse_inventory_connection(inv.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 22
    if not host or host == "YOUR_PUBLIC_IP":
        return None, port
    return host, port


def render_gitlab_ci_text(
    template: str,
    *,
    domain: str,
    deploy_host: str | None = None,
    deploy_port: int | None = None,
    install_prefix: str | None = None,
) -> str:
    text = template.replace("_example.local", domain)
    if deploy_host:
        text = text.replace("YOUR_PUBLIC_IP", deploy_host)
    if deploy_port is not None:
        text = re.sub(
            r'(DEPLOY_PORT:\s*")\d+(")',
            rf"\g<1>{int(deploy_port)}\2",
            text,
            count=1,
        )
    if install_prefix:
        text = text.replace("/opt/prod", install_prefix.rstrip("/"))
    return text


def strip_certresolver(compose_text: str) -> str:
    return CERTRESOLVER_LINE_RE.sub("", compose_text)


def strip_db_from_php_compose(compose_text: str) -> str:
    """Remove MySQL network and DB_* environment; keep APP_KEY / session settings."""
    text = re.sub(r"(?m)^\s+DB_[A-Z_]+:.*\n", "", compose_text)
    text = re.sub(r"(?m)^\s+-\s+db\s*\n", "", text)
    text = re.sub(r"(?m)^  db:\n(?:    .*\n)*", "", text)
    return text


def append_env_key(path: Path, key: str, *, placeholder: str = "__CHANGE_ME__") -> bool:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if key in parse_env_keys(text):
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"{key}={placeholder}\n"
    path.write_text(text, encoding="utf-8")
    return True


def upsert_env_key(path: Path, key: str, value: str) -> None:
    """Set key=value in an env file (create or replace)."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines(keepends=True)
    prefix = f"{key}="
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix):
            new_lines.append(f"{key}={value}\n")
            replaced = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")
    if not replaced:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append(f"{key}={value}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(new_lines), encoding="utf-8")


def remove_env_key(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if not line.strip().startswith(f"{key}=")]
    if len(kept) == len(lines):
        return False
    path.write_text("".join(kept), encoding="utf-8")
    return True


def _site_dir(repo_root: Path, domain: str) -> Path:
    return repo_root / "app/sites" / normalize_domain(domain)


def sites_needing_db(repo_root: Path) -> list[str]:
    """FQDNs under app/sites/ whose compose injects a DB password (db-provision)."""
    sites_root = repo_root / "app/sites"
    if not sites_root.is_dir():
        return []
    found: list[str] = []
    # Active compose key only — ignore commented PHP/DB stubs.
    active_db_password = re.compile(r"(?m)^[ \t]*DB_PASSWORD\s*:")
    for path in sorted(sites_root.iterdir()):
        if not path.is_dir() or path.name in RESERVED_SITE_DIRS:
            continue
        if path.name.startswith("_"):
            continue
        compose = path / "docker-compose.yml"
        if not compose.is_file():
            continue
        text = compose.read_text(encoding="utf-8")
        if not active_db_password.search(text):
            continue
        found.append(path.name)
    return found


def normalize_php_version(version: str) -> str:
    value = version.strip()
    if value not in PHP_VERSIONS:
        raise SiteError(
            f"unsupported PHP version {version!r} (expected {' or '.join(sorted(PHP_VERSIONS))})"
        )
    return value


def _template_dir(
    repo_root: Path,
    *,
    php: bool,
    php_version: str = DEFAULT_PHP_VERSION,
    node: bool = False,
) -> Path:
    if php and node:
        raise SiteError("PHP and Node are mutually exclusive")
    if node:
        return repo_root / "app/sites" / "_example_nuxt"
    if not php:
        return repo_root / "app/sites" / "_example"
    ver = normalize_php_version(php_version)
    name = "_example_php82" if ver == "8.2" else "_example_php"
    return repo_root / "app/sites" / name


def _check_traefik_hosts(repo_root: Path) -> None:
    dupes = find_duplicate_host_rules(compose_host_globs(repo_root))
    if dupes:
        raise SiteError(f"duplicate Traefik Host() rules: {', '.join(dupes)}")


def _compose_config_check(repo_root: Path, domain: str, env_key: str) -> None:
    compose_bin = shutil.which("docker")
    if not compose_bin:
        return
    app = repo_root / "app"
    env_base = app / ".env.test"
    if not env_base.is_file():
        env_base = app / ".env.test.example"
    if not env_base.is_file():
        return
    env_text = env_base.read_text(encoding="utf-8")
    if env_key and env_key not in parse_env_keys(env_text):
        env_text = env_text.rstrip() + f"\n{env_key}=test-only-secret\n"
    compose_file = _site_dir(repo_root, domain) / "docker-compose.yml"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
        tmp.write(env_text)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                tmp_path,
                "-f",
                str(compose_file.relative_to(app)),
                "config",
                "-q",
            ],
            cwd=app,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SiteError(f"docker compose config failed for {domain}: {detail}")


def _ask_yn(prompt: str) -> bool:
    while True:
        raw = input(f"{prompt} [y/n]: ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please answer y or n.")


def _ask_php_version() -> str:
    while True:
        raw = input("PHP version (8.2/8.4): ").strip()
        try:
            return normalize_php_version(raw)
        except SiteError as exc:
            print(exc)


def prompt_site_add() -> dict[str, object]:
    """Interactive questions for site-add. Returns kwargs for site_add()."""
    domain = input("Domain name: ").strip()
    php = _ask_yn("Enable PHP?")
    sql = False
    php_version = DEFAULT_PHP_VERSION
    node = False
    if php:
        php_version = _ask_php_version()
        sql = _ask_yn("Attach MySQL database?")
    else:
        node = _ask_yn("Enable Node (Nuxt SSR)?")
    lets_encrypt = _ask_yn("Get Let's Encrypt certificate?")
    return {
        "domain": domain,
        "php": php,
        "php_version": php_version,
        "node": node,
        "sql": sql,
        "lets_encrypt": lets_encrypt,
    }


def site_add(
    repo_root: Path,
    domain: str,
    *,
    php: bool = False,
    php_version: str = DEFAULT_PHP_VERSION,
    node: bool = False,
    sql: bool = False,
    db_name: str = "",
    db_password: str = "",
    lets_encrypt: bool = True,
    force: bool = False,
) -> dict[str, str]:
    """Scaffold app/sites/<fqdn> from _example / _example_php / _example_php82 / _example_nuxt.

    MySQL name/user always match db-provision (domain → ident). ``db_name`` is ignored.
    """
    fqdn = normalize_domain(domain)
    slug = domain_slug(fqdn)
    ident = db_ident_from_domain(fqdn)
    env_key = db_env_key(ident)
    app_key_name = app_env_key(ident)
    db_ident = ident  # must match db-provision (domain → underscores); db_name ignored
    _ = db_name
    resolved_php_version = (
        normalize_php_version(php_version) if php else DEFAULT_PHP_VERSION
    )

    if php and node:
        raise SiteError("PHP and Node are mutually exclusive")
    if sql and not php:
        raise SiteError("MySQL attach requires PHP (static and Node sites have no DB connection)")

    dest = _site_dir(repo_root, fqdn)
    if dest.exists():
        if not force:
            raise SiteError(f"site already exists: {dest.relative_to(repo_root)}")
        shutil.rmtree(dest)

    template = _template_dir(
        repo_root, php=php, php_version=resolved_php_version, node=node
    )
    if not template.is_dir():
        raise SiteError(f"missing template {template.relative_to(repo_root)}")

    env_example = repo_root / "app/.env.example"
    env_test_example = repo_root / "app/.env.test.example"
    env_file = repo_root / "app/.env"
    if sql:
        db_password = resolve_db_password(env_file, env_key, db_password)

    shutil.copytree(template, dest)
    compose_path = dest / "docker-compose.yml"
    compose = render_compose_text(
        compose_path.read_text(encoding="utf-8"),
        domain=fqdn,
        slug=slug,
        db_ident=db_ident,
        db_env_key_name=env_key,
        app_env_key_name=app_key_name,
    )
    if not lets_encrypt:
        compose = strip_certresolver(compose)
    if php and not sql:
        compose = strip_db_from_php_compose(compose)
    compose_path.write_text(compose, encoding="utf-8")

    nginx_path = dest / "nginx/nginx.conf"
    if nginx_path.is_file():
        nginx_path.write_text(
            render_nginx_text(nginx_path.read_text(encoding="utf-8"), slug=slug),
            encoding="utf-8",
        )
    gitlab_ci_path = dest / "site-repo" / ".gitlab-ci.yml"
    deploy_host, deploy_port = inventory_deploy_target(repo_root)
    if gitlab_ci_path.is_file():
        gitlab_ci_path.write_text(
            render_gitlab_ci_text(
                gitlab_ci_path.read_text(encoding="utf-8"),
                domain=fqdn,
                deploy_host=deploy_host,
                deploy_port=deploy_port,
                install_prefix=str(install_root()),
            ),
            encoding="utf-8",
        )

    env_added = False
    test_added = False
    env_secrets_written = False
    if php:
        upsert_env_key(env_file, app_key_name, generate_app_key())
        append_env_key(env_example, app_key_name)
        append_env_key(env_test_example, app_key_name, placeholder="base64:test-only-app-key")
        env_secrets_written = True
    if sql:
        env_added = append_env_key(env_example, env_key)
        test_added = append_env_key(
            env_test_example, env_key, placeholder="test-only-secret"
        )
        upsert_env_key(env_file, env_key, db_password)
        env_secrets_written = True

    _check_traefik_hosts(repo_root)
    _compose_config_check(repo_root, fqdn, env_key if sql else (app_key_name if php else ""))

    dns_target = deploy_host or "YOUR_PUBLIC_IP"
    git_step = (
        "Git: paste site-repo/.gitlab-ci.yml, Play ▶"
        if deploy_host
        else "Git: paste site-repo/.gitlab-ci.yml, set DEPLOY_HOST, Play ▶"
    )
    next_steps = [
        "git add/commit/push the new site dir",
        "Ship the site slot (rsync app/sites/<fqdn> or your own CI)",
        git_step,
        f"DNS A record → {dns_target} (manual at registrar)",
    ]
    if php:
        next_steps.insert(
            0,
            f"APP_KEY in app/.env as {app_key_name}; site-add syncs to GitHub",
        )
    if sql:
        next_steps.insert(
            0 if not php else 1,
            f"DB password auto-set in app/.env as {env_key}; Site slot creates MySQL DB/user `{db_ident}`",
        )
    if not lets_encrypt:
        next_steps.append(
            f"If purchased PEM (not server wildcard): ssl/{fqdn}/ or certs-staging/{fqdn}/ "
            f"then scripts/deploy-wildcard-cert.sh {fqdn} — not GitHub deploy; "
            f"*.example.com one-level needs no PEM"
        )

    return {
        "domain": fqdn,
        "slug": slug,
        "site_dir": str(dest.relative_to(repo_root)),
        "php": str(php),
        "php_version": resolved_php_version if php else "",
        "node": str(node),
        "sql": str(sql),
        "lets_encrypt": str(lets_encrypt),
        "db_ident": db_ident,
        "db_env_key": env_key if sql else "",
        "app_env_key": app_key_name if php else "",
        "env_example_added": str(env_added),
        "env_test_example_added": str(test_added),
        "env_password_written": str(env_secrets_written),
        "env_secrets_written": str(env_secrets_written),
        "next_steps": " | ".join(next_steps),
    }


def site_rm(repo_root: Path, domain: str, *, force: bool = False) -> dict[str, str]:
    fqdn = normalize_domain(domain)
    dest = _site_dir(repo_root, fqdn)
    if not dest.is_dir():
        raise SiteError(f"site not found: {dest.relative_to(repo_root)}")

    ident = db_ident_from_domain(fqdn)
    env_key = db_env_key(ident)
    if not force:
        raise SiteError("site-rm requires --force")

    shutil.rmtree(dest)

    env_removed = remove_env_key(repo_root / "app/.env.example", env_key)
    test_removed = remove_env_key(repo_root / "app/.env.test.example", env_key)
    # Do not auto-delete password from app/.env (human secret file).

    _check_traefik_hosts(repo_root)

    return {
        "domain": fqdn,
        "site_dir": str(dest.relative_to(repo_root)),
        "env_example_removed": str(env_removed),
        "env_test_example_removed": str(test_removed),
    }
