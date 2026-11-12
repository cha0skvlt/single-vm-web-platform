"""Terraform fmt/validate (service layer, no cloud API)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.runners import ToolMissing, require_tool, run


def run_fmt_check(repo_root: Path) -> None:
    require_tool("terraform")
    proc = run(["terraform", "fmt", "-check", "-recursive"], cwd=repo_root / "terraform")
    assert proc.returncode == 0, proc.stderr or proc.stdout


def run_validate(repo_root: Path) -> None:
    require_tool("terraform")
    tf = repo_root / "terraform"
    init = run(["terraform", "init", "-backend=false", "-reconfigure"], cwd=tf)
    assert init.returncode == 0, init.stderr or init.stdout
    val = run(["terraform", "validate"], cwd=tf)
    assert val.returncode == 0, val.stderr or val.stdout


@pytest.mark.service
def test_terraform_fmt(repo_root: Path):
    try:
        run_fmt_check(repo_root)
    except ToolMissing:
        pytest.skip("terraform not available")


@pytest.mark.service
def test_terraform_validate(repo_root: Path):
    try:
        run_validate(repo_root)
    except ToolMissing:
        pytest.skip("terraform not available")
