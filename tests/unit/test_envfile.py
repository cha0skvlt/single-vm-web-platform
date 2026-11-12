from __future__ import annotations

from pathlib import Path

import pytest

from scripts.envfile import (
    missing_env_keys,
    parse_env_keys,
    read_env_value,
    render_deploy_env,
)


@pytest.mark.unit
def test_env_example_complete(repo_root: Path):
    text = (repo_root / "app/.env.example").read_text(encoding="utf-8")
    assert "PROJECT_HOSTNAME=prod" in text
    assert missing_env_keys(text) == []


@pytest.mark.unit
def test_env_test_example_complete(repo_root: Path):
    text = (repo_root / "app/.env.test.example").read_text(encoding="utf-8")
    assert missing_env_keys(text) == []


@pytest.mark.unit
def test_parse_env_keys_skips_comments():
    keys = parse_env_keys("# foo\nBAR=1\n")
    assert keys == {"BAR"}


@pytest.mark.unit
def test_render_deploy_env_strips_comments_and_pins_prod_host():
    raw = (
        "# comment\n"
        "PROJECT_HOSTNAME=prod\n"
        "PROD_PUBLIC_HOST=prod.example\n"
        "PROD_ADMIN_PASSWORD=secret\n"
    )
    out = render_deploy_env(raw)
    assert "#" not in out
    assert "PROD_PUBLIC_HOST=status.example.com\n" in out
    assert "PROD_PUBLIC_HOST=prod.example" not in out
    assert "PROD_ADMIN_PASSWORD=secret" in out


@pytest.mark.unit
def test_render_deploy_env_skips_lines_without_equals():
    raw = "PROJECT_HOSTNAME=prod\nNO_EQUALS_HERE\nFOO=bar\n"
    out = render_deploy_env(raw)
    assert "NO_EQUALS_HERE" not in out
    assert "FOO=bar" in out


@pytest.mark.unit
def test_render_deploy_env_extra_keys_sorted_after_order():
    raw = "PROJECT_HOSTNAME=prod\nZZZ_EXTRA=val\nAAA_EXTRA=val2\n"
    out = render_deploy_env(raw)
    lines = [l for l in out.splitlines() if l.startswith(("ZZZ", "AAA"))]
    assert lines == ["AAA_EXTRA=val2", "ZZZ_EXTRA=val"]


@pytest.mark.unit
def test_read_env_value_without_shell_expansion():
    text = "PROJECT_HOSTNAME=prod\nTRAEFIK_DASHBOARD_AUTH=admin:$$2y$$05$$\n"
    assert read_env_value(text, "PROJECT_HOSTNAME") == "prod"
    assert "$$2y$$05$$" in (read_env_value(text, "TRAEFIK_DASHBOARD_AUTH") or "")


@pytest.mark.unit
def test_read_env_value_quoted():
    assert read_env_value('FOO="bar baz"\n', "FOO") == "bar baz"


@pytest.mark.unit
def test_read_env_value_missing_and_comments():
    assert read_env_value("# only\n", "FOO") is None
    assert read_env_value("BAR=1\n", "FOO") is None
