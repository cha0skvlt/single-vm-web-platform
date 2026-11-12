"""Ansible handler contracts — notify targets must resolve before prod deploy."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _handlers_from_role(handlers_path: Path) -> tuple[set[str], list[str]]:
    data = yaml.safe_load(handlers_path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        return set(), [f"{handlers_path}: handler root must be a list"]

    registered: set[str] = set()
    violations: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("block"):
            violations.append(
                f"{handlers_path}: handler {entry.get('name')!r} uses block; "
                "use listen: on flat tasks (Ansible 2.21+ does not resolve block handlers)"
            )
            continue
        listen = entry.get("listen")
        if isinstance(listen, str):
            registered.add(listen)
        elif isinstance(listen, list):
            registered.update(str(item) for item in listen)
        name = entry.get("name")
        if isinstance(name, str) and not listen:
            registered.add(name)
    return registered, violations


def _collect_notifies(node: object, path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        notify = node.get("notify")
        if isinstance(notify, str):
            out.append((path, notify))
        elif isinstance(notify, list):
            out.extend((path, str(item)) for item in notify)
        for key in ("tasks", "block", "rescue", "always"):
            nested = node.get(key)
            if isinstance(nested, list):
                for index, item in enumerate(nested):
                    out.extend(_collect_notifies(item, f"{path}.{key}[{index}]"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            out.extend(_collect_notifies(item, f"{path}[{index}]"))
    return out


def verify_ansible_handlers(repo_root: Path) -> None:
    roles_dir = repo_root / "ansible/roles"
    missing: list[str] = []
    violations: list[str] = []

    for role_dir in sorted(roles_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        handlers_path = role_dir / "handlers/main.yml"
        if not handlers_path.is_file():
            continue
        registered, role_violations = _handlers_from_role(handlers_path)
        violations.extend(role_violations)

        tasks_dir = role_dir / "tasks"
        if not tasks_dir.is_dir():
            continue
        for task_file in sorted(tasks_dir.glob("*.yml")):
            tasks = yaml.safe_load(task_file.read_text(encoding="utf-8")) or []
            for loc, notify in _collect_notifies(tasks, str(task_file.relative_to(repo_root))):
                if notify not in registered:
                    missing.append(f"{loc}: notify {notify!r} not in {handlers_path.relative_to(repo_root)}")

    assert not violations, "invalid handler definitions:\n" + "\n".join(violations)
    assert not missing, "unresolved handler notify targets:\n" + "\n".join(missing)


@pytest.mark.service
def test_ansible_role_handlers_resolve_notify(repo_root: Path):
    verify_ansible_handlers(repo_root)
