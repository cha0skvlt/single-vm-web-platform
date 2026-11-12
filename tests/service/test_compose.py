"""Docker Compose static validation (service layer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.app_stack import compose_env_file_arg, prepare_app_stack
from tests.support.runners import ToolMissing, require_tool, run


def run_base_config(repo_root: Path) -> None:
    require_tool("docker")
    app = repo_root / "app"
    prepare_app_stack(app)
    proc = run(["docker", "compose", *compose_env_file_arg(app), "config", "-q"], cwd=app)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def run_example_config(repo_root: Path) -> None:
    require_tool("docker")
    app = repo_root / "app"
    prepare_app_stack(app)
    proc = run(
        [
            "docker",
            "compose",
            *compose_env_file_arg(app),
            "-f",
            "sites/_example/docker-compose.yml",
            "config",
            "-q",
        ],
        cwd=app,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.service
def test_compose_base_stack(repo_root: Path):
    try:
        run_base_config(repo_root)
    except ToolMissing:
        pytest.skip("docker not available")


def run_example_nuxt_config(repo_root: Path) -> None:
    require_tool("docker")
    app = repo_root / "app"
    prepare_app_stack(app)
    proc = run(
        [
            "docker",
            "compose",
            *compose_env_file_arg(app),
            "-f",
            "sites/_example_nuxt/docker-compose.yml",
            "config",
            "-q",
        ],
        cwd=app,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.service
def test_compose_example_nuxt_site(repo_root: Path):
    try:
        run_example_nuxt_config(repo_root)
    except ToolMissing:
        pytest.skip("docker not available")


def run_prod_status_config(repo_root: Path) -> None:
    require_tool("docker")
    app = repo_root / "app"
    prepare_app_stack(app)
    proc = run(
        [
            "docker",
            "compose",
            *compose_env_file_arg(app),
            "-f",
            "sites/ops-panel/docker-compose.yml",
            "config",
            "-q",
        ],
        cwd=app,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.service
def test_compose_prod_status_site(repo_root: Path):
    try:
        run_prod_status_config(repo_root)
    except ToolMissing:
        pytest.skip("docker not available")
