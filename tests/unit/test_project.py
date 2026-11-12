from __future__ import annotations

from pathlib import Path

import pytest

from scripts.project import ENV_KEY, app_install_root, install_root, project_hostname, project_hostname_or


@pytest.mark.unit
def test_project_hostname_reads_env(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "myhost")
    assert project_hostname() == "myhost"


@pytest.mark.unit
def test_project_hostname_or_default(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert project_hostname_or() == "prod"
    monkeypatch.setenv(ENV_KEY, "prod")
    assert project_hostname_or() == "prod"


@pytest.mark.unit
def test_install_paths(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "prod")
    assert install_root() == Path("/opt/prod")
    assert app_install_root() == Path("/opt/prod/app")


@pytest.mark.unit
def test_project_hostname_missing(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(RuntimeError, match=ENV_KEY):
        project_hostname()
