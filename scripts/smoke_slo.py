"""Post-deploy SLO helpers for smoke-slo.sh (Traefik 5xx ratio gate)."""

from __future__ import annotations


def evaluate_5xx_ratio(
    *,
    five_xx_rate: float,
    total_rate: float,
    max_ratio: float,
    min_total_rate: float,
) -> tuple[bool, str]:
    """Return (ok, detail). Skip ratio when traffic is below min_total_rate."""
    if total_rate <= 0:
        return True, "no request rate"
    if total_rate < min_total_rate:
        return True, f"low traffic ({total_rate:.6f} req/s < {min_total_rate})"
    ratio = five_xx_rate / total_rate
    if ratio > max_ratio:
        return False, f"5xx ratio {ratio:.4f} > {max_ratio:.4f} ({five_xx_rate:.6f}/{total_rate:.6f} req/s)"
    return True, f"5xx ratio {ratio:.4f} <= {max_ratio:.4f}"
