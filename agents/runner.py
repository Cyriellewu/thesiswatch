"""Run the two active virtual agents and execute local SQLite trades."""

from __future__ import annotations

import logging
import sqlite3

from agents.context import account_row, load_agent_context
from agents.decision_engine import decide_for_account, mark_cycle_et_date, meta_cycle_date_et, ny_today_iso
from agents.execution_engine import execute_decision
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers

log = logging.getLogger(__name__)

DEFAULT_AGENTS = [
    {"agent_id": "takeover-balanced", "style": "balanced"},
    {"agent_id": "fresh-balanced", "style": "balanced"},
]


def run_all_agents(
    conn: sqlite3.Connection,
    px_map: dict[str, float] | None = None,
    *,
    force_review: bool = False,
) -> list[dict]:
    """Run active virtual agents.

    Buy & Hold remains a benchmark and is intentionally not included here.
    """

    holdings = load_watchlist_tickers()
    opps = load_opportunity_tickers()
    bundle = load_agent_context(conn, holdings=holdings, opportunity_syms=opps)
    for key, value in (px_map or {}).items():
        try:
            fv = float(value)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            bundle.px[str(key).upper()] = fv

    today = ny_today_iso()
    first_daily = force_review or meta_cycle_date_et(conn) != today
    urgent = any(str(a.get("level")) == "urgent" for a in bundle.alerts)
    results: list[dict] = []

    for cfg in DEFAULT_AGENTS:
        aid = cfg["agent_id"]
        try:
            meta = decide_for_account(
                conn,
                bundle,
                bundle.px,
                account_id=aid,
                first_daily=first_daily,
                urgent_market=urgent,
            )
            if meta.get("skipped"):
                results.append({"agent_id": aid, "ok": True, "skipped": True})
                continue

            if meta.get("failed"):
                results.append(
                    {
                        "agent_id": aid,
                        "ok": False,
                        "decision_id": meta.get("decision_id"),
                        "error": (meta.get("norm") or {}).get("plain_reason"),
                    }
                )
                continue

            trade_results: list[dict] = []
            legs_all: list[str] = []
            for norm in meta.get("norms") or [meta.get("norm") or {}]:
                if norm.get("action") == "HOLD":
                    continue
                legs = execute_decision(
                    conn,
                    account_id=aid,
                    decision_id=int(meta["decision_id"]),
                    normalized=dict(norm),
                    px_map=bundle.px,
                    rules=meta["rules"],
                    ctx=bundle,
                    style=meta.get("style"),
                    mode=str(meta["mode"]),
                    urgent_market=urgent,
                )
                legs_all.extend(legs)
                for leg in legs:
                    trade_results.append({"ok": True, "message": leg})

            results.append(
                {
                    "agent_id": aid,
                    "ok": True,
                    "decision_id": meta.get("decision_id"),
                    "summary": (meta.get("norm") or {}).get("plain_reason", ""),
                    "decision_count": len(meta.get("norms") or [meta.get("norm")]),
                    "executed_count": len(legs_all),
                    "failed_count": 0,
                    "trade_results": trade_results,
                }
            )
        except Exception as exc:
            log.exception("agent %s crashed", aid)
            results.append({"agent_id": aid, "ok": False, "error": str(exc), "crashed": True})

    if first_daily:
        mark_cycle_et_date(conn, today)
    return results


if __name__ == "__main__":
    import json
    import sys

    from db.client import get_conn, init_schema

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    db_conn = get_conn(read_only=False) if len(sys.argv) <= 1 else sqlite3.connect(sys.argv[1])
    db_conn.row_factory = sqlite3.Row
    try:
        init_schema(db_conn)
        rows = run_all_agents(db_conn, force_review=True)
        db_conn.commit()
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    finally:
        db_conn.close()
