"""按最新行情为所有虚拟账户落一条权益快照（现金 + 持仓市值）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _ts_iso() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def snapshot_all_agent_accounts(conn: sqlite3.Connection, price_by_symbol: dict[str, float], ts: str | None = None) -> int:
    """返回写入的 snapshot 条数。"""

    stamp = ts or _ts_iso()
    n = 0
    accts = conn.execute("SELECT id, cash_usd FROM agent_accounts").fetchall()
    for acct in accts:
        aid = acct["id"]
        cash = float(acct["cash_usd"])
        mv = 0.0
        pos_rows = conn.execute(
            "SELECT symbol, qty FROM agent_positions WHERE account_id = ?",
            (aid,),
        ).fetchall()
        for pr in pos_rows:
            sym = str(pr["symbol"]).upper()
            px = float(price_by_symbol.get(sym) or 0.0)
            mv += float(pr["qty"]) * px
        eq = cash + mv
        conn.execute(
            """INSERT INTO agent_equity_snapshots (account_id, ts, equity_usd, cash_usd, positions_mv_usd)
               VALUES (?,?,?,?,?)""",
            (aid, stamp, eq, cash, mv),
        )
        n += 1
    return n
