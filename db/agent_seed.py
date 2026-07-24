"""Seed local virtual agents (no broker balance); books come from `config/watchlist.yaml`."""

from __future__ import annotations

import json
import sqlite3

import yaml

from data_layer.portfolio_analytics import load_positions
from db.client import repo_root


def _virtual_takeover_cash_usd() -> float:
    p = repo_root() / "config" / "watchlist.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    try:
        return float(raw.get("virtual_takeover_cash_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def seed_virtual_agents_if_empty(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM agent_accounts").fetchone()["n"]:
        return

    pos_rows = load_positions()
    tcash = _virtual_takeover_cash_usd()

    g_bal = '{"horizon":"3m","objective":"preserve_then_grow","risk_mode":"risk_aware"}'
    g_cons = '{"horizon":"6m","objective":"preserve_then_grow","risk_mode":"capital_first"}'
    g_agg = '{"horizon":"2w","objective":"maximize_return","risk_mode":"risk_aware"}'
    g_to = '{"horizon":"1m","objective":"maximize_return","risk_mode":"risk_aware"}'
    g_bh = '{"horizon":"1y","objective":"preserve_then_grow","risk_mode":"capital_first"}'

    agents = [
        ("fresh-conservative", "Fresh · 保守（$5k）", "fresh", "conservative", 5000.0, 5000.0, 1, {"max_single_pct": 15, "min_cash_pct": 20}, g_cons),
        ("fresh-balanced", "Fresh · 平衡（$5k）", "fresh", "balanced", 5000.0, 5000.0, 1, {"max_single_pct": 25, "min_cash_pct": 10}, g_bal),
        ("fresh-aggressive", "Fresh · 激进（$5k）", "fresh", "aggressive", 5000.0, 5000.0, 1, {"max_single_pct": 35, "min_cash_pct": 5}, g_agg),
        ("takeover-balanced", "Takeover · 平衡接管", "takeover", "balanced", tcash, tcash, 1, {"max_single_pct": 25, "min_cash_pct": 10}, g_to),
        ("buyhold", "对照 · 不动了", "takeover", None, tcash, tcash, 0, {}, g_bh),
    ]

    conn.executemany(
        """INSERT INTO agent_accounts (id, label, mode, style, cash_usd, starting_cash_usd, allow_trades, rules_json, agent_goal_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            (aid, lab, mode, st, cash, start, allow, json.dumps(rules), gj)
            for aid, lab, mode, st, cash, start, allow, rules, gj in agents
        ],
    )

    for p in pos_rows:
        sym = p.ticker.upper()
        conn.execute(
            """INSERT INTO agent_positions (account_id, symbol, qty, avg_cost_usd)
               VALUES ('takeover-balanced',?,?,?),
                      ('buyhold',?,?,?)
               ON CONFLICT(account_id, symbol) DO UPDATE SET
                   qty = excluded.qty,
                   avg_cost_usd = excluded.avg_cost_usd""",
            (sym, p.qty, p.avg_cost_per_share, sym, p.qty, p.avg_cost_per_share),
        )
