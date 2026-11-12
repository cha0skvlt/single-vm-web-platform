"""Shell script static analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.runners import ToolMissing, require_tool, run


def run_shellcheck(repo_root: Path) -> None:
    require_tool("shellcheck")
    scripts = sorted((repo_root / "scripts").glob("*.sh"))
    assert scripts, "no scripts to check"
    proc = run(["shellcheck", *[str(s) for s in scripts]])
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.service
def test_scripts_shellcheck(repo_root: Path):
    try:
        run_shellcheck(repo_root)
    except ToolMissing:
        pytest.skip("shellcheck not available")
