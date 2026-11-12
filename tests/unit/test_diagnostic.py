"""LOG-3 diagnostic bundle tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.diagnostic import (
    SNAPSHOT_TYPES,
    build_output_name,
    collect_manifest,
    infer_level,
    is_snapshot_record,
    normalize_log_event,
    should_include_line,
    write_bundle,
)
from scripts.diagnostic_redact import redact_string

@pytest.mark.unit
def test_manifest_first_line_and_valid_schema(tmp_path: Path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "certs").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "scripts.diagnostic.collect_manifest",
        lambda **_: {
            "type": "manifest",
            "ts": "2026-06-03T12:00:00.000Z",
            "schema_version": "2",
            "hostname": "test",
            "bundle_mode": "compact",
            "log_hours": 1,
            "deploy_sha": "deadbeef",
        },
    )
    monkeypatch.setattr("scripts.diagnostic.collect_snapshot_records", lambda **_: [])

    def sources(_hours: int):
        yield "ops-panel", json.dumps(
            {
                "ts": "2026-06-03T12:00:00.000Z",
                "level": "warn",
                "service": "ops-panel",
                "event": "test.event",
                "msg": "oops",
                "attrs": {},
                "schema_version": "1",
            }
        )
        yield "traefik", '{"password":"LEAK","level":"info","msg":"x"}'

    out = tmp_path / "bundle.ndjson"
    write_bundle(
        out,
        app_root=app_root,
        repo_root=repo,
        hours=1,
        full=False,
        log_sources=sources,
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    assert first["type"] == "manifest"
    assert first["schema_version"] == "2"
    assert "LEAK" not in out.read_text(encoding="utf-8")
    assert Path(f"{out}.sha256").is_file()

    for line in lines[1:]:
        event = json.loads(line)
        if is_snapshot_record(event):
            assert event["type"] in SNAPSHOT_TYPES
            continue
        assert isinstance(event, dict) and "level" in event


@pytest.mark.unit
def test_compact_filter_keeps_warn_and_tail():
    assert should_include_line('{"level":"error"}', full=False, compact_tail=2, line_index=0, total=10)
    assert not should_include_line('{"level":"info","msg":"x"}', full=False, compact_tail=2, line_index=0, total=10)
    assert should_include_line('{"level":"info","msg":"x"}', full=False, compact_tail=2, line_index=9, total=10)


@pytest.mark.unit
def test_infer_level_from_json_and_text():
    assert infer_level('{"level":"warn"}') == "warn"
    assert infer_level("ERROR something failed") == "error"


@pytest.mark.unit
def test_normalize_preserves_log1():
    raw = {
        "ts": "2026-06-03T12:00:00.000Z",
        "level": "info",
        "service": "ops-panel",
        "event": "http.access",
        "msg": "ok",
        "attrs": {},
        "schema_version": "1",
    }
    line = json.dumps(raw)
    assert normalize_log_event(line, source="docker") == raw


@pytest.mark.unit
def test_build_output_name():
    assert build_output_name("status.example.com").startswith("diagnostic-status-")


@pytest.mark.unit
def test_bounded_log_lines_cap(monkeypatch):
    from scripts.diagnostic import _bounded_log_lines, _count_log_lines, max_input_lines

    def src(_hours: int):
        for i in range(10):
            yield "svc", f"line{i}"

    monkeypatch.setenv("DIAGNOSTIC_MAX_INPUT_LINES", "3")
    assert max_input_lines() == 3
    assert _count_log_lines(src, 1) == 3
    assert len(list(_bounded_log_lines(src, 1))) == 3


@pytest.mark.unit
def test_max_input_lines_invalid_env(monkeypatch):
    from scripts.diagnostic import max_input_lines

    monkeypatch.setenv("DIAGNOSTIC_MAX_INPUT_LINES", "nope")
    assert max_input_lines() == 100_000
    assert max_input_lines(runtime="panel", full=False) == 2500
    monkeypatch.delenv("DIAGNOSTIC_MAX_INPUT_LINES", raising=False)
    assert max_input_lines() == 100_000


@pytest.mark.unit
def test_manifest_schema_v2(tmp_path: Path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".deploy-sha").write_text("abc123\n", encoding="utf-8")
    monkeypatch.setattr("scripts.diagnostic.docker_available", lambda: False)
    monkeypatch.setattr("scripts.diagnostic.detect_runtime", lambda: "panel")
    monkeypatch.setattr("scripts.diagnostic.collect_health_snapshot", lambda _: {"type": "health"})
    monkeypatch.setattr("scripts.diagnostic.collect_sites_snapshot", lambda _: {"type": "sites"})
    monkeypatch.setattr("scripts.diagnostic.collect_fail2ban_snapshot", lambda _: {"type": "fail2ban"})
    monkeypatch.setattr("scripts.diagnostic.collect_prometheus_snapshot", lambda: {"type": "prometheus"})
    monkeypatch.setattr("scripts.diagnostic.collect_traefik_snapshot", lambda: {"type": "traefik"})
    monkeypatch.setattr("scripts.diagnostic.collect_node_snapshot", lambda: {"type": "node"})
    manifest = collect_manifest(app_root=app_root, repo_root=repo, hours=6, full=False)
    assert manifest["schema_version"] == "2"
    assert manifest["deploy_sha"] == "abc123"
    assert manifest["runtime"] == "panel"
    assert manifest["log_sources"] == ["loki"]


@pytest.mark.unit
def test_redact_string_non_json_assignment():
    assert "SECRET" not in redact_string("password=SECRET")


@pytest.mark.unit
def test_is_snapshot_record():
    assert is_snapshot_record({"type": "health"})
    assert not is_snapshot_record({"event": "log.line"})
