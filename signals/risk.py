"""Stops, reward/risk, position size (Elder Money layer, simplified)."""

from __future__ import annotations

from typing import Any


def planned_stop(summary: dict[str, Any], *, max_loss_pct: float = 0.07) -> float:
    """Stop: max(recent swing cushion, fixed % below close)."""
    c = float(summary.get("close") or 0.0)
    swing = float(summary.get("swing_low_20") or c * 0.92)
    pct_stop = c * (1.0 - max_loss_pct)
    # tighter = higher stop price
    return max(swing * 0.998, pct_stop)


def reward_risk(entry: float, stop: float, target: float) -> float | None:
    if entry <= stop or target <= entry:
        return None
    risk = entry - stop
    reward = target - entry
    return reward / risk if risk > 0 else None


def suggest_shares(*, account_equity: float, risk_budget_pct: float, entry: float, stop: float) -> int | None:
    if account_equity <= 0 or entry <= 0 or stop <= 0 or entry <= stop:
        return None
    risk_per_share = entry - stop
    dollars_risk = account_equity * risk_budget_pct
    if risk_per_share <= 0:
        return None
    sh = int(dollars_risk // risk_per_share)
    return max(0, sh)
