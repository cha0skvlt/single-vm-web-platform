"""Subprocess runners for service/integration checks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ToolMissing(RuntimeError):
    pass


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolMissing(f"{name} not in PATH")
    return path


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    import os

    run_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def ansible_inventory_from_example(ansible_dir: Path) -> Path:
    from scripts.inventory import materialize_inventory_example

    example = ansible_dir / "inventory.ini.example"
    inv = ansible_dir / ".inventory.ci.tmp"
    text = materialize_inventory_example(example.read_text(encoding="utf-8"))
    inv.write_text(text.replace("YOUR_PUBLIC_IP", "127.0.0.1"), encoding="utf-8")
    return inv
