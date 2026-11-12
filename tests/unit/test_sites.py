"""Unit tests for scripts.sites (OPS-2)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from scripts import cli
from scripts.sites import (
    SiteError,
    append_env_key,
    build_site_provision_sql,
    db_env_key,
    db_ident_from_domain,
    domain_slug,
    generate_db_password,
    inventory_deploy_target,
    normalize_domain,
    prompt_site_add,
    remove_env_key,
    render_compose_text,
    render_gitlab_ci_text,
    render_nginx_text,
    resolve_db_password,
    site_add,
    site_rm,
    sites_needing_db,
)


@pytest.mark.unit
def test_normalize_domain_ok():
    assert normalize_domain("Example.RU.") == "example.ru"


@pytest.mark.unit
def test_normalize_domain_invalid():
    with pytest.raises(SiteError, match="invalid domain"):
        normalize_domain("not-a-domain")


@pytest.mark.unit
def test_normalize_domain_reserved():
    with pytest.raises(SiteError, match="invalid domain"):
        normalize_domain("_example")


@pytest.mark.unit
def test_slug_and_keys():
    assert domain_slug("example.ru") == "example-ru"
    assert db_ident_from_domain("example.ru") == "example_ru"
    assert db_env_key("example_ru") == "EXAMPLE_RU_DB_PASSWORD"


@pytest.mark.unit
def test_db_ident_from_domain_normalizes():
    assert db_ident_from_domain("Example.RU.") == "example_ru"


@pytest.mark.unit
def test_build_site_provision_sql():
    sql = build_site_provision_sql("example_ru", "s3cret")
    assert "CREATE DATABASE IF NOT EXISTS `example_ru`" in sql
    assert "CREATE USER IF NOT EXISTS 'example_ru'@'%'" in sql
    assert "IDENTIFIED BY 's3cret'" in sql
    assert "GRANT ALL PRIVILEGES ON `example_ru`.*" in sql


@pytest.mark.unit
def test_sql_literal_escapes_backslash_and_quote():
    from scripts.sites import sql_literal

    assert sql_literal("a\\") == "'a\\\\'"
    assert sql_literal("o'hare") == "'o''hare'"


@pytest.mark.unit
def test_build_site_provision_sql_backslash_password():
    sql = build_site_provision_sql("example_ru", "x\\")
    assert "IDENTIFIED BY 'x\\\\'" in sql


@pytest.mark.unit
def test_render_compose_text(repo_root: Path):
    template = (repo_root / "app/sites/_example/docker-compose.yml").read_text(encoding="utf-8")
    out = render_compose_text(
        template,
        domain="example.ru",
        slug="example-ru",
        db_ident="example_ru",
        db_env_key_name="EXAMPLE_RU_DB_PASSWORD",
    )
    assert "Host(`example.ru`)" in out
    assert "example-ru-web:" in out
    assert "var/site-src/example.ru:/usr/share/nginx/html:ro" in out
    assert "example-internal" not in out
    assert "_example.local" not in out
    assert "example-ru-nginx-exporter:" in out
    exporter_block = out.split("example-ru-nginx-exporter:", 1)[1].split("\n\n", 1)[0]
    assert "- web" in exporter_block


@pytest.mark.unit
def test_sites_needing_db(tmp_path: Path):
    sites = tmp_path / "app/sites"
    (sites / "static.ru").mkdir(parents=True)
    (sites / "static.ru" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (sites / "shop.ru").mkdir(parents=True)
    (sites / "shop.ru" / "docker-compose.yml").write_text(
        "services:\n  x:\n    environment:\n      DB_PASSWORD: ${SHOP_RU_DB_PASSWORD}\n",
        encoding="utf-8",
    )
    (sites / "stub.ru").mkdir(parents=True)
    (sites / "stub.ru" / "docker-compose.yml").write_text(
        "services:\n  x:\n    #     DB_PASSWORD: ${STUB_RU_DB_PASSWORD}\n",
        encoding="utf-8",
    )
    (sites / "_example_php").mkdir(parents=True)
    (sites / "_example_php" / "docker-compose.yml").write_text(
        "DB_PASSWORD: ${EXAMPLE_DB_PASSWORD}\n", encoding="utf-8"
    )
    assert sites_needing_db(tmp_path) == ["shop.ru"]


@pytest.mark.unit
def test_site_add_php_sql_le(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    shutil.copytree(repo_root / "app/sites/_example_php", tmp_path / "app/sites/_example_php")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    result = site_add(
        tmp_path,
        "shop.example.com",
        php=True,
        sql=True,
        db_password="s3cret",
        lets_encrypt=True,
    )
    compose = (tmp_path / "app/sites/shop.example.com/docker-compose.yml").read_text()
    assert "shop-example-com-php:" in compose
    assert "DB_DATABASE: shop_example_com" in compose
    assert "DB_USERNAME: shop_example_com" in compose
    assert "APP_KEY: ${SHOP_EXAMPLE_COM_APP_KEY}" in compose
    assert "SESSION_DRIVER: file" in compose
    assert "build:" in compose
    assert (tmp_path / "app/sites/shop.example.com/php/Dockerfile").is_file()
    assert "certresolver=le" in compose
    assert "  db:" in compose
    env = (tmp_path / "app/.env").read_text()
    assert "SHOP_EXAMPLE_COM_DB_PASSWORD=s3cret" in env
    assert "SHOP_EXAMPLE_COM_APP_KEY=base64:" in env
    assert result["env_password_written"] == "True"


@pytest.mark.unit
def test_generate_db_password_is_nonempty():
    pw = generate_db_password()
    assert len(pw) >= 32
    assert "=" not in pw  # token_urlsafe alphabet


@pytest.mark.unit
def test_resolve_db_password_reuse_existing(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("SHOP_RU_DB_PASSWORD=keep-me\n", encoding="utf-8")
    assert resolve_db_password(env, "SHOP_RU_DB_PASSWORD") == "keep-me"
    assert resolve_db_password(env, "SHOP_RU_DB_PASSWORD", "override") == "override"


@pytest.mark.unit
def test_site_add_php_sql_auto_password(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    shutil.copytree(repo_root / "app/sites/_example_php", tmp_path / "app/sites/_example_php")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    result = site_add(
        tmp_path,
        "auto.example.com",
        php=True,
        sql=True,
    )
    env = (tmp_path / "app/.env").read_text()
    line = [
        ln
        for ln in env.splitlines()
        if ln.startswith("AUTO_EXAMPLE_COM_DB_PASSWORD=")
    ][0]
    pw = line.split("=", 1)[1]
    assert pw
    assert result["env_password_written"] == "True"


@pytest.mark.unit
def test_prompt_site_add_no_password_prompt(monkeypatch):
    answers = iter(["morphology.example.com", "y", "8.4", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    out = prompt_site_add()
    assert out["domain"] == "morphology.example.com"
    assert out["php"] is True
    assert out["php_version"] == "8.4"
    assert out["node"] is False
    assert out["sql"] is True
    assert "db_name" not in out
    assert "db_password" not in out


@pytest.mark.unit
def test_prompt_site_add_php82(monkeypatch):
    answers = iter(["shop.example.com", "y", "8.2", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    out = prompt_site_add()
    assert out["php_version"] == "8.2"
    assert out["node"] is False
    assert out["sql"] is True


@pytest.mark.unit
def test_site_add_php82_sql(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    shutil.copytree(repo_root / "app/sites/_example_php", tmp_path / "app/sites/_example_php")
    shutil.copytree(
        repo_root / "app/sites/_example_php82", tmp_path / "app/sites/_example_php82"
    )
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    result = site_add(
        tmp_path,
        "shop.example.com",
        php=True,
        php_version="8.2",
        sql=True,
        db_password="s3cret",
        lets_encrypt=True,
    )
    compose = (tmp_path / "app/sites/shop.example.com/docker-compose.yml").read_text()
    assert "image: shop-example-com-php:8.2" in compose
    dockerfile = (tmp_path / "app/sites/shop.example.com/php/Dockerfile").read_text()
    assert "FROM php:8.2-fpm-alpine" in dockerfile
    assert result["php_version"] == "8.2"


@pytest.mark.unit
def test_site_add_php_no_sql_no_le(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example_php", tmp_path / "app/sites/_example_php")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    site_add(
        tmp_path,
        "app.example.com",
        php=True,
        sql=False,
        lets_encrypt=False,
    )
    compose = (tmp_path / "app/sites/app.example.com/docker-compose.yml").read_text()
    assert "app-example-com-php:" in compose
    assert "tls.certresolver=le" not in compose
    assert "DB_DATABASE:" not in compose
    assert "APP_KEY: ${APP_EXAMPLE_COM_APP_KEY}" in compose
    assert "SESSION_DRIVER: file" in compose
    assert re.search(r"(?m)^  db:\s*$", compose) is None
    assert "- db" not in compose
    assert "certresolver=le" not in [
        ln.strip() for ln in compose.splitlines() if ln.strip().startswith("-")
    ]
    nginx = (tmp_path / "app/sites/app.example.com/nginx/nginx.conf").read_text()
    assert "root /usr/share/nginx/html/public;" in nginx
    assert r"location ~ /\." in nginx
    assert "APP_EXAMPLE_COM_APP_KEY=base64:" in (tmp_path / "app/.env").read_text()


@pytest.mark.unit
def test_site_add_sql_requires_php(tmp_path: Path):
    with pytest.raises(SiteError, match="requires PHP"):
        site_add(
            tmp_path,
            "x.example.com",
            php=False,
            sql=True,
            db_name="x",
            db_password="p",
        )


@pytest.mark.unit
def test_site_add_php_and_node_rejected(tmp_path: Path):
    with pytest.raises(SiteError, match="mutually exclusive"):
        site_add(tmp_path, "x.example.com", php=True, node=True)


@pytest.mark.unit
def test_prompt_site_add_node(monkeypatch):
    answers = iter(["kitett.example.com", "n", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    out = prompt_site_add()
    assert out["php"] is False
    assert out["node"] is True
    assert out["sql"] is False


@pytest.mark.unit
def test_render_compose_text_nuxt(repo_root: Path):
    template = (repo_root / "app/sites/_example_nuxt/docker-compose.yml").read_text(
        encoding="utf-8"
    )
    out = render_compose_text(
        template,
        domain="kitett.example.com",
        slug="kitett-example-com",
        db_ident="kitett_example_com",
        db_env_key_name="KITETT_EXAMPLE_COM_DB_PASSWORD",
    )
    assert "kitett-example-com-node:" in out
    assert "image: kitett-example-com-node:18" in out
    assert "loadbalancer.server.port=3000" in out
    assert "Host(`kitett.example.com`)" in out
    assert "var/site-src/kitett.example.com" in out
    assert "dockerfile: ../../../app/sites/kitett.example.com/node/Dockerfile" in out
    assert "example-node" not in out
    assert "_example.local" not in out
    assert "DB_PASSWORD" not in out


@pytest.mark.unit
def test_site_add_node(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example_nuxt", tmp_path / "app/sites/_example_nuxt")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    result = site_add(
        tmp_path,
        "kitett.example.com",
        node=True,
        lets_encrypt=True,
    )
    site_dir = tmp_path / "app/sites/kitett.example.com"
    compose = (site_dir / "docker-compose.yml").read_text()
    assert result["node"] == "True"
    assert result["php"] == "False"
    assert "kitett-example-com-node:" in compose
    assert "loadbalancer.server.port=3000" in compose
    assert "certresolver=le" in compose
    assert (site_dir / "node/Dockerfile").is_file()
    assert "dockerfile: ../../../app/sites/kitett.example.com/node/Dockerfile" in compose
    gitlab_ci = (site_dir / "site-repo" / ".gitlab-ci.yml").read_text()
    assert 'SITE_DOMAIN: "kitett.example.com"' in gitlab_ci
    assert "--exclude=node_modules" in gitlab_ci
    assert "--exclude=.output" in gitlab_ci
    assert "--exclude=.nuxt" in gitlab_ci
    assert "KITETT_EXAMPLE_COM_APP_KEY" not in (tmp_path / "app/.env.example").read_text()


@pytest.mark.unit
def test_render_nginx_text():
    text = "#     fastcgi_pass example-php:9000;"
    assert "example-ru-php:9000" in render_nginx_text(text, slug="example-ru")


@pytest.mark.unit
def test_render_gitlab_ci_text_fills_inventory():
    raw = (
        'SITE_DOMAIN: "_example.local"\n'
        'DEPLOY_HOST: "YOUR_PUBLIC_IP"\n'
        'DEPLOY_PORT: "22"\n'
        "  /opt/prod/scripts/deploy-site.sh\n"
    )
    out = render_gitlab_ci_text(
        raw,
        domain="shop.example.com",
        deploy_host="203.0.113.10",
        deploy_port=2222,
        install_prefix="/opt/lab",
    )
    assert 'SITE_DOMAIN: "shop.example.com"' in out
    assert 'DEPLOY_HOST: "203.0.113.10"' in out
    assert 'DEPLOY_PORT: "2222"' in out
    assert "/opt/lab/scripts/deploy-site.sh" in out
    assert "YOUR_PUBLIC_IP" not in out


@pytest.mark.unit
def test_inventory_deploy_target_skips_placeholder(tmp_path: Path):
    (tmp_path / "ansible").mkdir()
    (tmp_path / "ansible/inventory.ini").write_text(
        "[prod]\nvm ansible_host=YOUR_PUBLIC_IP ansible_user=deploy ansible_port=22\n",
        encoding="utf-8",
    )
    assert inventory_deploy_target(tmp_path) == (None, 22)
    (tmp_path / "ansible/inventory.ini").write_text(
        "[prod]\nvm ansible_host=203.0.113.10 ansible_user=deploy ansible_port=2222\n",
        encoding="utf-8",
    )
    assert inventory_deploy_target(tmp_path) == ("203.0.113.10", 2222)


@pytest.mark.unit
def test_env_key_helpers(tmp_path: Path):
    path = tmp_path / ".env.example"
    path.write_text("FOO=1\n", encoding="utf-8")
    assert append_env_key(path, "BAR") is True
    assert append_env_key(path, "BAR") is False
    assert remove_env_key(path, "BAR") is True
    assert remove_env_key(path, "BAR") is False


@pytest.mark.unit
def test_site_add_and_rm(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")

    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    result = site_add(tmp_path, "example.ru")
    assert result["slug"] == "example-ru"
    assert result["php"] == "False"
    assert result["node"] == "False"
    assert result["sql"] == "False"
    assert result["lets_encrypt"] == "True"
    site_dir = tmp_path / "app/sites/example.ru"
    assert site_dir.is_dir()
    site_compose = (site_dir / "docker-compose.yml").read_text(encoding="utf-8")
    assert "Host(`example.ru`)" in site_compose
    assert "certresolver=le" in site_compose
    gitlab_ci = (site_dir / "site-repo" / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert 'SITE_DOMAIN: "example.ru"' in gitlab_ci
    assert "_example.local" not in gitlab_ci
    assert "--exclude=.gitlab-ci.yml" in gitlab_ci
    assert 'DEPLOY_HOST: "YOUR_PUBLIC_IP"' in gitlab_ci
    assert 'DEPLOY_PORT: "22"' in gitlab_ci
    nginx = (site_dir / "nginx/nginx.conf").read_text(encoding="utf-8")
    assert r"location ~ /\." in nginx
    # static default: no DB password stubs
    assert "EXAMPLE_RU_DB_PASSWORD" not in (tmp_path / "app/.env.example").read_text()

    with pytest.raises(SiteError, match="requires --force"):
        site_rm(tmp_path, "example.ru", force=False)

    site_rm(tmp_path, "example.ru", force=True)
    assert not site_dir.exists()


@pytest.mark.unit
def test_site_add_fills_gitlab_ci_from_inventory(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")
    (tmp_path / "ansible").mkdir()
    (tmp_path / "ansible/inventory.ini").write_text(
        "[prod]\nvm ansible_host=203.0.113.10 ansible_user=deploy ansible_port=2222\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)

    result = site_add(tmp_path, "shop.example.com")
    gitlab_ci = (tmp_path / "app/sites/shop.example.com/site-repo/.gitlab-ci.yml").read_text(
        encoding="utf-8"
    )
    assert 'DEPLOY_HOST: "203.0.113.10"' in gitlab_ci
    assert 'DEPLOY_PORT: "2222"' in gitlab_ci
    assert "203.0.113.10" in result["next_steps"]


@pytest.mark.unit
def test_site_add_simple(tmp_path: Path, monkeypatch):
    example = tmp_path / "app/sites/_example"
    example.mkdir(parents=True)
    (example / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)
    site_add(tmp_path, "lab.example.com")
    assert (tmp_path / "app/sites/lab.example.com").is_dir()


@pytest.mark.unit
def test_site_add_missing_template(tmp_path: Path):
    with pytest.raises(SiteError, match="missing template"):
        site_add(tmp_path, "example.ru")


@pytest.mark.unit
def test_site_add_duplicate_traefik(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    dup = 'traefik.http.routers.x.rule=Host(`dup.local`)\n'
    (tmp_path / "app/docker-compose.yml").write_text(dup, encoding="utf-8")
    (tmp_path / "app/sites/other").mkdir(parents=True)
    (tmp_path / "app/sites/other/docker-compose.yml").write_text(dup, encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)
    with pytest.raises(SiteError, match="duplicate Traefik"):
        site_add(tmp_path, "example.ru")


@pytest.mark.unit
def test_site_rm_reserved(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("scripts.sites.RESERVED_SITE_DIRS", frozenset({"evil.example.com"}))
    with pytest.raises(SiteError, match="reserved"):
        site_rm(tmp_path, "evil.example.com", force=True)


@pytest.mark.unit
def test_append_env_key_no_trailing_newline(tmp_path: Path):
    path = tmp_path / ".env.example"
    path.write_text("FOO=1", encoding="utf-8")
    assert append_env_key(path, "BAR") is True
    assert path.read_text(encoding="utf-8") == "FOO=1\nBAR=__CHANGE_ME__\n"


@pytest.mark.unit
def test_compose_config_check_no_env_file(tmp_path: Path, monkeypatch):
    from scripts.sites import _compose_config_check

    app = tmp_path / "app"
    site = app / "sites/example.ru"
    site.mkdir(parents=True)
    (site / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr("scripts.sites.shutil.which", lambda _: "/usr/bin/docker")
    _compose_config_check(tmp_path, "example.ru", "EXAMPLE_RU_DB_PASSWORD")


@pytest.mark.unit
def test_cli_site_add_ok(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)
    assert cli.main(["site-add", "example.ru"]) == 0


@pytest.mark.unit
def test_cli_site_add_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.main(["site-add", "bad"]) == 1


@pytest.mark.unit
def test_cli_site_rm_force(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/example.ru")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("EXAMPLE_RU_DB_PASSWORD=x\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.main(["site-rm", "example.ru", "--force"]) == 0


@pytest.mark.unit
def test_compose_config_check_no_docker(tmp_path: Path, monkeypatch):
    from scripts.sites import _compose_config_check

    monkeypatch.setattr("scripts.sites.shutil.which", lambda _: None)
    _compose_config_check(tmp_path, "example.ru", "EXAMPLE_RU_DB_PASSWORD")


@pytest.mark.unit
def test_compose_config_check_success(tmp_path: Path, monkeypatch):
    from scripts.sites import _compose_config_check

    app = tmp_path / "app"
    site = app / "sites/example.ru"
    site.mkdir(parents=True)
    (site / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (app / ".env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("scripts.sites.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("scripts.sites.subprocess.run", lambda *a, **k: Proc())
    _compose_config_check(tmp_path, "example.ru", "EXAMPLE_RU_DB_PASSWORD")


@pytest.mark.unit
def test_cli_site_rm_requires_force(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/example.ru")
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.main(["site-rm", "example.ru"]) == 1


@pytest.mark.unit
def test_site_add_force_overwrite(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/example.ru")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)
    site_add(tmp_path, "example.ru", force=True)
    assert (tmp_path / "app/sites/example.ru").is_dir()


@pytest.mark.unit
def test_site_add_exists_without_force(tmp_path: Path, monkeypatch, repo_root: Path):
    (tmp_path / "app/sites").mkdir(parents=True)
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/_example")
    shutil.copytree(repo_root / "app/sites/_example", tmp_path / "app/sites/example.ru")
    (tmp_path / "app/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "app/.env.example").write_text("", encoding="utf-8")
    (tmp_path / "app/.env.test.example").write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.sites._compose_config_check", lambda *a, **k: None)
    with pytest.raises(SiteError, match="already exists"):
        site_add(tmp_path, "example.ru")


@pytest.mark.unit
def test_site_rm_not_found(tmp_path: Path):
    with pytest.raises(SiteError, match="not found"):
        site_rm(tmp_path, "missing.example.com", force=True)


@pytest.mark.unit
def test_normalize_domain_reserved_fqdn(monkeypatch):
    monkeypatch.setattr("scripts.sites.RESERVED_SITE_DIRS", frozenset({"evil.example.com"}))
    with pytest.raises(SiteError, match="reserved"):
        normalize_domain("evil.example.com")


@pytest.mark.unit
def test_append_env_key_new_file(tmp_path: Path):
    path = tmp_path / "new.env"
    assert append_env_key(path, "SITE_KEY") is True
    assert "SITE_KEY=" in path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_compose_config_check_uses_env_test(tmp_path: Path, monkeypatch):
    from scripts.sites import _compose_config_check

    app = tmp_path / "app"
    site = app / "sites/example.ru"
    site.mkdir(parents=True)
    (site / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (app / ".env.test").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("scripts.sites.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("scripts.sites.subprocess.run", lambda *a, **k: Proc())
    _compose_config_check(tmp_path, "example.ru", "EXAMPLE_RU_DB_PASSWORD")


@pytest.mark.unit
def test_compose_config_check_failure(tmp_path: Path, monkeypatch):
    from scripts.sites import _compose_config_check

    app = tmp_path / "app"
    site = app / "sites/example.ru"
    site.mkdir(parents=True)
    (site / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (app / ".env.test.example").write_text("PROJECT_HOSTNAME=prod\n", encoding="utf-8")

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "bad compose"

    monkeypatch.setattr("scripts.sites.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("scripts.sites.subprocess.run", lambda *a, **k: Proc())
    with pytest.raises(SiteError, match="docker compose config failed"):
        _compose_config_check(tmp_path, "example.ru", "EXAMPLE_RU_DB_PASSWORD")
