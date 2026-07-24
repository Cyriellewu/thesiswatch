"""Setup / pivot / extension heuristics (VCP-lite, not chart pattern ML)."""

from __future__ import annotations

from typing import Any


def pivot_level(summary: dict[str, Any]) -> float:
    """Short-term pivot proxy: recent 20-session high."""
    return float(summary.get("pivot_20h") or summary.get("close") or 0.0)


def setup_metrics(
    summary: dict[str, Any],
    *,
    near_pct: float = 0.03,
    extended_pct: float = 0.08,
    vol_ratio: float = 1.5,
) -> dict[str, Any]:
    c = float(summary.get("close") or 0.0)
    pivot = pivot_level(summary) or c
    dist = (c - pivot) / pivot if pivot else 0.0
    vol = float(summary.get("vol") or 0.0)
    v50 = float(summary.get("vol50") or 1.0) or 1.0
    vol_ok = vol > v50 * vol_ratio

    near_pivot = abs(dist) <= near_pct and c <= pivot * 1.001
    breakout = c > pivot * 1.001
    extended = dist > extended_pct
    vol_contract = vol < v50 * 0.85

    m50 = summary.get("ma50")
    volume_breakdown = False
    if m50 is not None:
        m50f = float(m50)
        if c < m50f * 0.985 and vol > v50 * 1.25:
            volume_breakdown = True

    # VCP-lite: last three local max drawdowns approximated via rolling max drawdown from peaks — keep very light.
    vcp_like = bool(summary.get("ma50")) and float(summary["close"]) > float(summary["ma50"]) and vol_contract

    return {
        "pivot": pivot,
        "dist_to_pivot_pct": dist * 100.0,
        "near_pivot": near_pivot,
        "breakout": breakout,
        "volume_confirm": vol_ok,
        "extended": extended,
        "vcp_like": vcp_like,
        "volume_breakdown": volume_breakdown,
    }
