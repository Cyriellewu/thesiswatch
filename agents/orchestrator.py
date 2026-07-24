"""Compatibility wrapper: market_pulse still calls this module."""
from __future__ import annotations

import sqlite3

from agents.runner import run_all_agents


def run_agent_cycle(
    conn: sqlite3.Connection,
    px_map: dict[str, float] | None = None,
    *,
    push_trade_ntfy: bool = False,
    force_review: bool = False,
) -> dict:
    _ = push_trade_ntfy
    rows = run_all_agents(conn=conn, px_map=px_map or {}, force_review=force_review)
    decisions = sum(1 for r in rows if not r.get("skipped"))
    trade_legs = 0
    for r in rows:
        for t in r.get("trade_results") or []:
            if t.get("ok"):
                trade_legs += 1
    return {"decisions": decisions, "trade_legs": trade_legs, "ntfy": 0, "accounts": rows}
