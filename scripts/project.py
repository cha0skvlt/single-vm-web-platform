"""PROJECT_HOSTNAME — single source for infra resource naming."""

from __future__ import annotations

import os
from pathlib import Path

ENV_KEY = "PROJECT_HOSTNAME"
DEFAULT_HOSTNAME = "prod"


def project_hostname() -> str:
    value = os.environ.get(ENV_KEY, "").strip()
    if not value:
        raise RuntimeError(f"{ENV_KEY} is not set (source app/.env or scripts/with-project-env.sh)")
    return value


def project_hostname_or(default: str = DEFAULT_HOSTNAME) -> str:
    value = os.environ.get(ENV_KEY, "").strip()
    return value or default


def install_root(*, hostname: str | None = None) -> Path:
    name = (hostname or project_hostname_or()).strip() or DEFAULT_HOSTNAME
    return Path(f"/opt/{name}")


def app_install_root(*, hostname: str | None = None) -> Path:
    return install_root(hostname=hostname) / "app"

