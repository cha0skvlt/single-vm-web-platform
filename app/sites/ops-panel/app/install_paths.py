"""Server install paths from PROJECT_HOSTNAME (default prod → /opt/prod/app)."""

from __future__ import annotations

import os
from pathlib import Path

ENV_KEY = "PROJECT_HOSTNAME"
DEFAULT_HOSTNAME = "prod"


def project_hostname() -> str:
    value = os.environ.get(ENV_KEY, DEFAULT_HOSTNAME).strip()
    return value or DEFAULT_HOSTNAME


def install_root() -> Path:
    return Path(f"/opt/{project_hostname()}")


def app_root() -> Path:
    return install_root() / "app"
