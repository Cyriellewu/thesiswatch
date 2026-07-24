"""把本次脉冲的行情 + 库里信号写回 opportunity_candidates.score，触发「高分机会」门槛。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from data_layer.market_data import Quote


def _max_signal_recent(conn: sqlite3.Connection, symbol: str) -> int:
    r = conn.execute(
        """SELECT COALESCE(MAX(score), 0) AS mx FROM market_signals
           WHERE upper(symbol) = upper(?) AND datetime(created_at) > datetime('now', '-2 days')""",
        (symbol,),
    ).fetchone()
    return int(r["mx"]) if r else 0


def sync_opportunity_scores_with_quotes(
    conn: sqlite3.Connection,
    quotes: list[Quote],
) -> int:
    """按涨跌幅与近期 market_signals 给机会池 ticker 重写 score（1–99）；返回 UPDATE 次数（逐行计数）。"""
    rows = conn.execute("SELECT ticker, score, blurb FROM opportunity_candidates").fetchall()
    if not rows:
        return 0

    chg_by: dict[str, float] = {}
    for q in quotes:
        chg_by[str(q.symbol).upper()] = float(q.chg_pct or 0.0)

    n_updates = 0
    pulse_ts = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    for row in rows:
        sym = str(row["ticker"]).upper().strip()
        prior = int(row["score"] or 0)

        chg = abs(chg_by.get(sym, 0.0))
        move_pts = 0
        if chg >= 8.0:
            move_pts += 42
        elif chg >= 5.0:
            move_pts += 28
        elif chg >= 3.0:
            move_pts += 14

        sig_mx = _max_signal_recent(conn, sym)
        sig_pts = min(38, int(round(sig_mx * 0.35)))

        raw = move_pts + sig_pts
        new_score = int(min(99, max(1, raw)))

        if new_score <= 4 and sym in chg_by and prior > new_score:
            new_score = max(new_score, min(prior, 8))

        meta: dict[str, Any] = {
            "pulse_at": pulse_ts,
            "abs_daily_chg_pct": round(chg, 3),
            "move_pts": move_pts,
            "signal_peak_48h": sig_mx,
            "signal_pts": sig_pts,
        }

        conn.execute(
            """UPDATE opportunity_candidates SET score = ?, signals_json = ?, updated_at = datetime('now')
               WHERE upper(ticker) = upper(?)""",
            (new_score, json.dumps(meta, ensure_ascii=False), sym),
        )
        n_updates += 1

    return n_updates
