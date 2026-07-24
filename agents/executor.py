"""Execute validated agent decisions on virtual accounts (paper only)."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from agents.decision_schema import Action, AgentRoundDecision

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_cash(conn: sqlite3.Connection, account_id: str) -> float:
    row = conn.execute("SELECT cash_usd FROM agent_accounts WHERE id = ?", (account_id,)).fetchone()
    return float(row["cash_usd"] if row else 0.0)


def set_cash(conn: sqlite3.Connection, account_id: str, cash: float) -> None:
    conn.execute("UPDATE agent_accounts SET cash_usd = ?, updated_at = datetime('now') WHERE id = ?", (float(cash), account_id))


def get_position(conn: sqlite3.Connection, account_id: str, ticker: str) -> dict | None:
    row = conn.execute(
        "SELECT account_id, symbol, qty, avg_cost_usd FROM agent_positions WHERE account_id = ? AND symbol = ?",
        (account_id, ticker),
    ).fetchone()
    return dict(row) if row else None


def get_all_positions(conn: sqlite3.Connection, account_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT account_id, symbol, qty, avg_cost_usd FROM agent_positions WHERE account_id = ? AND qty > 0",
        (account_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _upsert_position(conn: sqlite3.Connection, account_id: str, ticker: str, qty: float, avg_cost: float) -> None:
    if qty <= 1e-9:
        conn.execute("DELETE FROM agent_positions WHERE account_id = ? AND symbol = ?", (account_id, ticker))
        return
    conn.execute(
        """
        INSERT INTO agent_positions (account_id, symbol, qty, avg_cost_usd, updated_at)
        VALUES (?,?,?,?,datetime('now'))
        ON CONFLICT(account_id, symbol) DO UPDATE SET
            qty = excluded.qty,
            avg_cost_usd = excluded.avg_cost_usd,
            updated_at = datetime('now')
        """,
        (account_id, ticker, float(qty), float(avg_cost)),
    )


def snapshot_equity(conn: sqlite3.Connection, account_id: str, price_provider) -> None:
    cash = get_cash(conn, account_id)
    positions = get_all_positions(conn, account_id)
    mv = 0.0
    for p in positions:
        px = float(price_provider(str(p["symbol"]).upper()) or 0.0)
        if px <= 0:
            px = float(p["avg_cost_usd"] or 0.0)
        mv += float(p["qty"]) * px
    eq = cash + mv
    conn.execute(
        """
        INSERT INTO agent_equity_snapshots (account_id, ts, equity_usd, cash_usd, positions_mv_usd)
        VALUES (?,?,?,?,?)
        """,
        (account_id, _now_iso(), eq, cash, mv),
    )


def execute_trade(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    decision_id: int,
    ticker: str,
    action: Action,
    shares: float,
    price: float,
    reasoning: str = "",
) -> dict:
    if shares <= 0 or price <= 0:
        return {"ok": False, "ticker": ticker, "message": "invalid shares or price"}
    shares = float(shares)
    price = float(price)
    cash = get_cash(conn, account_id)
    pos = get_position(conn, account_id, ticker)
    notional = shares * price

    if action in {Action.BUY, Action.ADD}:
        if cash + 1e-9 < notional:
            return {"ok": False, "ticker": ticker, "message": f"insufficient cash ${cash:.2f} < ${notional:.2f}"}
        if pos:
            old_qty = float(pos["qty"])
            old_cost = float(pos["avg_cost_usd"])
            new_qty = old_qty + shares
            new_cost = ((old_qty * old_cost) + (shares * price)) / max(new_qty, 1e-9)
            _upsert_position(conn, account_id, ticker, new_qty, new_cost)
        else:
            _upsert_position(conn, account_id, ticker, shares, price)
        set_cash(conn, account_id, cash - notional)
        side = "BUY"
    elif action in {Action.SELL, Action.TRIM}:
        if not pos or float(pos["qty"]) <= 0:
            return {"ok": False, "ticker": ticker, "message": "no position"}
        cur_qty = float(pos["qty"])
        exec_shares = min(shares, cur_qty)
        remain = cur_qty - exec_shares
        _upsert_position(conn, account_id, ticker, remain, float(pos["avg_cost_usd"]))
        set_cash(conn, account_id, cash + exec_shares * price)
        notional = exec_shares * price
        shares = exec_shares
        side = "SELL"
    else:
        return {"ok": False, "ticker": ticker, "message": f"non-tradable action: {action.value}"}

    conn.execute(
        """
        INSERT INTO agent_trades (account_id, ts, side, symbol, qty, px, notional_usd, reason, risk_note, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            account_id,
            _now_iso(),
            side,
            ticker,
            shares,
            price,
            notional,
            reasoning[:500],
            "",
            json.dumps({"decision_id": decision_id, "action": action.value}),
        ),
    )
    return {"ok": True, "ticker": ticker, "action": action.value, "shares": shares, "price": price, "notional": notional}


def execute_decision(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    decision: AgentRoundDecision,
    decision_id: int,
    price_provider,
) -> list[dict]:
    results: list[dict] = []
    for t in decision.decisions:
        if t.action in {Action.HOLD, Action.WAIT}:
            continue
        px = float(price_provider(t.ticker) or 0.0)
        if px <= 0:
            results.append({"ok": False, "ticker": t.ticker, "message": "no valid price"})
            continue
        if t.action in {Action.BUY, Action.ADD}:
            if t.entry_price_max and px > float(t.entry_price_max):
                results.append({"ok": False, "ticker": t.ticker, "message": f"price {px:.2f} > limit {t.entry_price_max:.2f}"})
                continue
            # size_pct means fraction of current equity
            cash = get_cash(conn, account_id)
            mv = 0.0
            for p in get_all_positions(conn, account_id):
                ppx = float(price_provider(str(p["symbol"]).upper()) or 0.0)
                if ppx <= 0:
                    ppx = float(p["avg_cost_usd"] or 0.0)
                mv += float(p["qty"]) * ppx
            equity = cash + mv
            notional = max(0.0, float(t.size_pct) * max(equity, 0.0))
            shares = notional / px if px > 0 else 0.0
            res = execute_trade(
                conn,
                account_id=account_id,
                decision_id=decision_id,
                ticker=t.ticker,
                action=t.action,
                shares=shares,
                price=px,
                reasoning=t.reasoning,
            )
        elif t.action == Action.SELL:
            pos = get_position(conn, account_id, t.ticker)
            if not pos:
                res = {"ok": False, "ticker": t.ticker, "message": "no position"}
            else:
                res = execute_trade(
                    conn,
                    account_id=account_id,
                    decision_id=decision_id,
                    ticker=t.ticker,
                    action=t.action,
                    shares=float(pos["qty"]),
                    price=px,
                    reasoning=t.reasoning,
                )
        else:  # TRIM
            pos = get_position(conn, account_id, t.ticker)
            if not pos:
                res = {"ok": False, "ticker": t.ticker, "message": "no position"}
            else:
                res = execute_trade(
                    conn,
                    account_id=account_id,
                    decision_id=decision_id,
                    ticker=t.ticker,
                    action=t.action,
                    shares=float(pos["qty"]) * 0.5,
                    price=px,
                    reasoning=t.reasoning,
                )
        results.append(res)
    snapshot_equity(conn, account_id, price_provider)
    return results
