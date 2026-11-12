"""Ansible playbook service checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.runners import ToolMissing, ansible_inventory_from_example, require_tool, run


def run_syntax_check(repo_root: Path) -> None:
    require_tool("ansible-playbook")
    ansible_dir = repo_root / "ansible"
    inv = ansible_inventory_from_example(ansible_dir)
    proc = run(
        ["ansible-playbook", "-i", str(inv), "playbook.yml", "--syntax-check"],
        cwd=ansible_dir,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.service
def test_playbook_syntax(repo_root: Path):
    try:
        run_syntax_check(repo_root)
    except ToolMissing:
        pytest.skip("ansible-playbook not available")
