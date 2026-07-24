"""聚合 agent 决策所需的只读上下文（来自 SQLite + 已抓取的行情）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AgentContextBundle:
    px: dict[str, float] = field(default_factory=dict)
    chg: dict[str, float] = field(default_factory=dict)
    signals: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    news_lines: list[str] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    macro_text: str = ""
    macro_risk_high: bool = False
    holdings: list[str] = field(default_factory=list)
    opportunity_syms: list[str] = field(default_factory=list)


def latest_quote_maps(conn: sqlite3.Connection) -> tuple[dict[str, float], dict[str, float]]:
    """symbol -> 最新价 / 涨跌幅%（按 `market_quotes` 最新一条）。"""

    rows = conn.execute(
        """
        SELECT m.symbol, m.px, m.chg_pct
        FROM market_quotes m
        INNER JOIN (
            SELECT symbol, MAX(retrieved_at) AS mx
            FROM market_quotes GROUP BY symbol
        ) t ON m.symbol = t.symbol AND m.retrieved_at = t.mx
        """
    ).fetchall()
    px: dict[str, float] = {}
    chg: dict[str, float] = {}
    for r in rows:
        sym = str(r["symbol"]).upper()
        px[sym] = float(r["px"] or 0)
        chg[sym] = float(r["chg_pct"] or 0)
    return px, chg


def load_agent_context(
    conn: sqlite3.Connection,
    *,
    holdings: list[str],
    opportunity_syms: list[str],
) -> AgentContextBundle:
    px, chg = latest_quote_maps(conn)
    sig_rows = conn.execute(
        """
        SELECT id, symbol, signal_type, payload_json, score, created_at
        FROM market_signals
        WHERE datetime(created_at) > datetime('now', '-2 days')
        ORDER BY score DESC, id DESC LIMIT 24
        """
    ).fetchall()
    signals: list[dict[str, Any]] = []
    for s in sig_rows:
        try:
            pj = json.loads(s["payload_json"] or "{}")
        except json.JSONDecodeError:
            pj = {}
        signals.append(
            {
                "id": int(s["id"]),
                "symbol": s["symbol"],
                "type": s["signal_type"],
                "score": int(s["score"] or 0),
                "payload": pj,
                "created_at": s["created_at"],
            }
        )

    alert_rows = conn.execute(
        """
        SELECT id, level, category, symbol, title, what_happened, why_matters, risk, occurred_at
        FROM alerts
        WHERE datetime(occurred_at) > datetime('now', '-2 days')
        ORDER BY
          CASE level WHEN 'urgent' THEN 0 WHEN 'major' THEN 1 WHEN 'attention' THEN 2 ELSE 3 END,
          occurred_at DESC
        LIMIT 16
        """
    ).fetchall()
    alerts = [dict(r) for r in alert_rows]

    news_rows = conn.execute(
        """
        SELECT COALESCE(symbol,'') AS symbol, headline
        FROM news_items
        ORDER BY datetime(created_at) DESC LIMIT 36
        """
    ).fetchall()
    news_lines = [f"{(r['symbol'] or '').upper()} — {r['headline']}" for r in news_rows]

    opp_rows = conn.execute(
        """SELECT ticker, score, bucket, blurb FROM opportunity_candidates ORDER BY score DESC, ticker LIMIT 24"""
    ).fetchall()
    opportunities = [dict(r) for r in opp_rows]

    macro_row = conn.execute(
        """SELECT payload_json FROM market_signals
           WHERE signal_type = 'macro_strip' ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    macro_text = ""
    macro_high = False
    if macro_row:
        try:
            mj = json.loads(macro_row["payload_json"] or "{}")
        except json.JSONDecodeError:
            mj = {}
        lvl = str(mj.get("_level") or "routine")
        macro_high = lvl in ("urgent", "major")
        macro_text = (
            f"VIX≈{mj.get('vix')} SPY {mj.get('spy_chg_pct')}% QQQ {mj.get('qqq_chg_pct')}% "
            f"10Y≈{mj.get('ten_year_yield_pct')}% 环境:{lvl}"
        )

    return AgentContextBundle(
        px=px,
        chg=chg,
        signals=signals,
        alerts=alerts,
        news_lines=news_lines,
        opportunities=opportunities,
        macro_text=macro_text,
        macro_risk_high=macro_high,
        holdings=[h.upper() for h in holdings],
        opportunity_syms=[o.upper() for o in opportunity_syms],
    )


def position_snapshot(conn: sqlite3.Connection, account_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT symbol, qty, avg_cost_usd FROM agent_positions WHERE account_id = ? ORDER BY symbol""",
        (account_id,),
    ).fetchall()
    return [{"symbol": str(r["symbol"]).upper(), "qty": float(r["qty"]), "avg": float(r["avg_cost_usd"])} for r in rows]


def account_row(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM agent_accounts WHERE id = ?", (account_id,)).fetchone()
