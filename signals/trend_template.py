"""Minervini-style trend template (simplified)."""

from __future__ import annotations

from typing import Any


def trend_flags(summary: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """Returns (trend_ok, strong_trend, reason_lines)."""
    reasons: list[str] = []
    c = float(summary.get("close") or 0.0)
    m20 = summary.get("ma20")
    m50 = summary.get("ma50")
    m200 = summary.get("ma200")
    slope = float(summary.get("ma200_slope") or 0.0)

    if not c or m50 is None or m200 is None:
        return False, False, ["均线数据不足，无法判定趋势模板。"]

    ma50, ma200 = float(m50), float(m200)
    trend_ok = c > ma50 and c > ma200 and ma50 > ma200 and slope > 0
    if trend_ok:
        reasons.append("Price > MA50 > MA200 且 MA200 斜率为正。")
    else:
        if c <= ma50:
            reasons.append("价格未稳定在 MA50 之上。")
        if c <= ma200:
            reasons.append("价格未在 MA200 之上。")
        if ma50 <= ma200:
            reasons.append("MA50 未高于 MA200（中期结构偏弱）。")
        if slope <= 0:
            reasons.append("MA200 斜率未向上。")

    strong = False
    if m20 is not None:
        m20f = float(m20)
        strong = trend_ok and c > m20f > ma50 > ma200
        if strong:
            reasons.append("更强结构：Price > MA20 > MA50 > MA200。")

    return trend_ok, strong, reasons
