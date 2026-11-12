"""smoke_slo.evaluate_5xx_ratio — CI-10 SLO gate helper."""

from __future__ import annotations

import pytest

from scripts.smoke_slo import evaluate_5xx_ratio


@pytest.mark.unit
def test_evaluate_5xx_ratio_skips_low_traffic():
    ok, detail = evaluate_5xx_ratio(
        five_xx_rate=1.0,
        total_rate=0.0005,
        max_ratio=0.05,
        min_total_rate=0.001,
    )
    assert ok is True
    assert "low traffic" in detail


@pytest.mark.unit
def test_evaluate_5xx_ratio_no_request_rate():
    ok, detail = evaluate_5xx_ratio(
        five_xx_rate=0.0,
        total_rate=0.0,
        max_ratio=0.05,
        min_total_rate=0.001,
    )
    assert ok is True
    assert detail == "no request rate"


@pytest.mark.unit
def test_evaluate_5xx_ratio_within_limit():
    ok, detail = evaluate_5xx_ratio(
        five_xx_rate=0.01,
        total_rate=1.0,
        max_ratio=0.05,
        min_total_rate=0.001,
    )
    assert ok is True
    assert "0.0100" in detail


@pytest.mark.unit
def test_evaluate_5xx_ratio_exceeds_limit():
    ok, detail = evaluate_5xx_ratio(
        five_xx_rate=0.2,
        total_rate=1.0,
        max_ratio=0.05,
        min_total_rate=0.001,
    )
    assert ok is False
    assert "0.2000" in detail
