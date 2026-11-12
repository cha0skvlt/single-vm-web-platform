from __future__ import annotations

import os
from pathlib import Path

import pytest

_APP_ENV = Path(__file__).resolve().parents[1] / "app" / ".env"


def pytest_configure(config) -> None:
    os.environ.setdefault("PROJECT_HOSTNAME", "prod")
    if _APP_ENV.is_file():
        config._platform_app_env_mtime_ns = _APP_ENV.stat().st_mtime_ns  # type: ignore[attr-defined]


def pytest_sessionfinish(session, exitstatus: int) -> None:
    baseline = getattr(session.config, "_platform_app_env_mtime_ns", None)
    if baseline is not None and _APP_ENV.is_file():
        assert _APP_ENV.stat().st_mtime_ns == baseline, (
            "app/.env was modified during tests — use app/.env.test in prepare_app_stack only"
        )


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_certs(tmp_path: Path) -> Path:
    root = tmp_path / "certs"
    root.mkdir()
    return root
