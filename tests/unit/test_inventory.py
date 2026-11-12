from __future__ import annotations

from pathlib import Path

import pytest

from scripts.inventory import materialize_inventory_example, validate_inventory


@pytest.mark.unit
def test_validate_inventory_example(repo_root: Path):
    text = (repo_root / "ansible/inventory.ini.example").read_text(encoding="utf-8")
    assert validate_inventory(materialize_inventory_example(text)) == []


@pytest.mark.unit
def test_validate_inventory_accepts_port_22():
    text = "[prod]\nvm ansible_host=1.2.3.4 ansible_user=deploy ansible_port=22\n"
    assert validate_inventory(text, hostname="prod") == []


@pytest.mark.unit
def test_validate_inventory_without_env(monkeypatch):
    monkeypatch.delenv("PROJECT_HOSTNAME", raising=False)
    assert "PROJECT_HOSTNAME" in validate_inventory("[x]\nx ansible_host=1 ansible_user=u ansible_port=666")[0]
