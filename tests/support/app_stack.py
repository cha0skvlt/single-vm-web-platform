"""Prepare app/ tree for docker compose config checks (never touches app/.env)."""

from __future__ import annotations

import shutil
from pathlib import Path

# CI/local compose tests only — production secrets live in app/.env (human-edited).
TEST_ENV_NAME = ".env.test"


def prepare_app_stack(app_dir: Path) -> None:
    env_template = app_dir / ".env.test.example"
    if not env_template.is_file():
        env_template = app_dir / ".env.example"
    env_test = app_dir / TEST_ENV_NAME
    if env_template.is_file():
        shutil.copy(env_template, env_test)
    for sub in ("certs/_default", "certs/_example.local", "traefik/dynamic"):
        (app_dir / sub).mkdir(parents=True, exist_ok=True)
    example_domain = app_dir / "traefik/dynamic/_example.local.yml"
    if not example_domain.is_file():
        example_domain.write_text(
            "tls:\n  certificates:\n"
            "    - certFile: /etc/traefik/dynamic/certs/_example.local/fullchain.pem\n"
            "      keyFile: /etc/traefik/dynamic/certs/_example.local/privkey.pem\n",
            encoding="utf-8",
        )


def compose_env_file_arg(app_dir: Path) -> list[str]:
    """--env-file for docker compose when cwd is app_dir."""
    test_env = app_dir / TEST_ENV_NAME
    if test_env.is_file():
        return ["--env-file", TEST_ENV_NAME]
    return []
