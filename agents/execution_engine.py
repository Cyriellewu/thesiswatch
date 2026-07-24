"""本地虚拟成交：不调用券商，只更新 SQLite。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from agents.context import AgentContextBundle, position_snapshot

NY = ZoneInfo("America/New_York")
MIN_TRADE_USD = 50.0
QTY_ROUND = 6


def _ts() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def _today_et() -> str:
    return datetime.now(NY).date().isoformat()


def account_equity(conn: sqlite3.Connection, account_id: str, px_map: dict[str, float]) -> tuple[float, float, float]:
    row = conn.execute("SELECT cash_usd FROM agent_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        return 0.0, 0.0, 0.0
    cash = float(row["cash_usd"])
    mv = 0.0
    for p in position_snapshot(conn, account_id):
        sym = p["symbol"]
        px = float(px_map.get(sym) or 0.0)
        mv += p["qty"] * px
    return cash + mv, cash, mv


def _max_buy_notional(
    *,
    equity: float,
    cash: float,
    sym: str,
    px: float,
    pos_qty: float,
    min_cash_pct: float,
    max_single_pct: float,
    desired: float,
) -> float:
    if px <= 0 or equity <= 0:
        return 0.0
    min_cash_floor = (min_cash_pct / 100.0) * equity
    room_cash = max(0.0, cash - min_cash_floor)
    cur_mv = pos_qty * px
    max_mv = (max_single_pct / 100.0) * equity
    room_single = max(0.0, max_mv - cur_mv)
    room_notional = min(room_cash, room_single)
    return max(0.0, min(desired, room_notional))


def _register_trade(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    side: str,
    action: str,
    symbol: str,
    qty: float,
    px: float,
    notional: float,
    plain_reason: str,
    plain_risk: str,
    decision_id: int,
) -> None:
    conn.execute(
        """INSERT INTO agent_trades
           (account_id, ts, side, symbol, qty, px, notional_usd, reason, risk_note, metadata_json,
            action, decision_id, plain_reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            account_id,
            _ts(),
            side.upper(),
            symbol.upper(),
            round(qty, QTY_ROUND),
            round(px, 6),
            round(notional, 2),
            plain_reason[:2000],
            plain_risk[:2000],
            None,
            action.upper(),
            decision_id,
            plain_reason[:2000],
        ),
    )


def trades_today_count(conn: sqlite3.Connection, account_id: str) -> int:
    d = _today_et()
    r = conn.execute(
        """SELECT COUNT(*) AS n FROM agent_trades
           WHERE account_id = ? AND substr(ts, 1, 10) = ?""",
        (account_id, d),
    ).fetchone()
    return int(r["n"] if r else 0)


def execute_decision(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    decision_id: int,
    normalized: dict,
    px_map: dict[str, float],
    rules: dict,
    ctx: AgentContextBundle,
    style: str | None,
    mode: str,
    urgent_market: bool,
    enforce_takeover_cap: bool = True,
) -> list[str]:
    """执行一条已规范化决策（含 ROTATE）；返回执行的腿描述列表。"""

    action = str(normalized.get("action") or "HOLD").upper()
    reason = str(normalized.get("plain_reason") or "（无说明）")
    risk = str(normalized.get("plain_risk") or "")
    msgs: list[str] = []

    if action == "HOLD":
        return msgs

    if (
        enforce_takeover_cap
        and mode == "takeover"
        and trades_today_count(conn, account_id) >= 2
        and not urgent_market
    ):
        return msgs  # silent skip — takeover 低频

    equity, cash, _ = account_equity(conn, account_id, px_map)

    max_single = float(rules.get("max_single_pct") or 25)
    min_cash = float(rules.get("min_cash_pct") or 10)

    if action == "BUY":
        sym = str(normalized.get("symbol") or "").upper()
        want = float(normalized.get("dollar_amount") or 0)
        px = float(px_map.get(sym) or 0)
        if sym not in px_map or px <= 0 or want < MIN_TRADE_USD:
            return msgs
        pos_qty = next((p["qty"] for p in position_snapshot(conn, account_id) if p["symbol"] == sym), 0.0)
        cap = _max_buy_notional(
            equity=equity,
            cash=cash,
            sym=sym,
            px=px,
            pos_qty=pos_qty,
            min_cash_pct=min_cash,
            max_single_pct=max_single,
            desired=want,
        )
        if cap < MIN_TRADE_USD:
            return msgs
        qty = cap / px
        notional = qty * px
        conn.execute("UPDATE agent_accounts SET cash_usd = cash_usd - ?, updated_at = datetime('now') WHERE id = ?", (notional, account_id))
        row = conn.execute(
            """SELECT qty, avg_cost_usd FROM agent_positions WHERE account_id=? AND symbol=?""",
            (account_id, sym),
        ).fetchone()
        if row:
            oq = float(row["qty"])
            oa = float(row["avg_cost_usd"])
            nq = oq + qty
            na = (oq * oa + notional) / nq if nq > 0 else px
            conn.execute(
                """UPDATE agent_positions SET qty = ?, avg_cost_usd = ?, updated_at = datetime('now')
                   WHERE account_id=? AND symbol=?""",
                (nq, na, account_id, sym),
            )
        else:
            conn.execute(
                """INSERT INTO agent_positions (account_id, symbol, qty, avg_cost_usd, updated_at)
                   VALUES (?,?,?,?, datetime('now'))""",
                (account_id, sym, qty, notional / qty if qty else px),
            )
        _register_trade(
            conn,
            account_id=account_id,
            side="buy",
            action="BUY",
            symbol=sym,
            qty=qty,
            px=px,
            notional=notional,
            plain_reason=reason,
            plain_risk=risk,
            decision_id=decision_id,
        )
        msgs.append(f"买入 {sym} 约 ${notional:.0f}")
        return msgs

    if action in ("SELL", "TRIM"):
        sym = str(normalized.get("symbol") or "").upper()
        px = float(px_map.get(sym) or 0)
        row = conn.execute(
            """SELECT qty, avg_cost_usd FROM agent_positions WHERE account_id=? AND symbol=?""",
            (account_id, sym),
        ).fetchone()
        if not row or px <= 0:
            return msgs
        oq = float(row["qty"])
        mv = oq * px
        target = mv if action == "SELL" else float(normalized.get("dollar_amount") or 0)
        if action == "TRIM" and target < MIN_TRADE_USD:
            return msgs
        notional = mv if action == "SELL" else min(target, mv * 0.999)
        if notional < MIN_TRADE_USD:
            return msgs
        qty = min(oq, notional / px)
        proceeds = qty * px
        nq = round(oq - qty, QTY_ROUND)
        conn.execute("UPDATE agent_accounts SET cash_usd = cash_usd + ?, updated_at = datetime('now') WHERE id = ?", (proceeds, account_id))
        if nq <= 1e-6:
            conn.execute("DELETE FROM agent_positions WHERE account_id=? AND symbol=?", (account_id, sym))
        else:
            conn.execute(
                """UPDATE agent_positions SET qty = ?, updated_at = datetime('now') WHERE account_id=? AND symbol=?""",
                (nq, account_id, sym),
            )
        leg = "SELL" if action == "SELL" else "TRIM"
        _register_trade(
            conn,
            account_id=account_id,
            side="sell",
            action=leg,
            symbol=sym,
            qty=qty,
            px=px,
            notional=proceeds,
            plain_reason=reason,
            plain_risk=risk,
            decision_id=decision_id,
        )
        msgs.append(f"{leg} {sym} 约 ${proceeds:.0f}")
        return msgs

    if action == "ROTATE":
        sym_f = str(normalized.get("symbol") or "").upper()
        sym_t = str(normalized.get("symbol_to") or normalized.get("rotate_into") or "").upper()
        leg_amt = float(normalized.get("dollar_amount") or 0)
        if leg_amt < MIN_TRADE_USD or not sym_f or not sym_t:
            return msgs
        sell_part = normalized.copy()
        sell_part.update({"action": "TRIM", "symbol": sym_f, "dollar_amount": leg_amt})
        msgs.extend(
            execute_decision(
                conn,
                account_id=account_id,
                decision_id=decision_id,
                normalized=sell_part,
                px_map=px_map,
                rules=rules,
                ctx=ctx,
                style=style,
                mode=mode,
                urgent_market=urgent_market,
                enforce_takeover_cap=False,
            )
        )
        if not msgs:
            return msgs
        row = conn.execute("SELECT cash_usd FROM agent_accounts WHERE id = ?", (account_id,)).fetchone()
        cash2 = float(row["cash_usd"]) if row else 0
        usable = max(0, cash2 - float(rules.get("min_cash_pct") or 10) / 100 * account_equity(conn, account_id, px_map)[0])
        buy_amt = min(leg_amt * 1.005, usable)
        if buy_amt < MIN_TRADE_USD:
            return msgs
        buy_part = normalized.copy()
        buy_part.update({"action": "BUY", "symbol": sym_t, "dollar_amount": buy_amt})
        msgs.extend(
            execute_decision(
                conn,
                account_id=account_id,
                decision_id=decision_id,
                normalized=buy_part,
                px_map=px_map,
                rules=rules,
                ctx=ctx,
                style=style,
                mode=mode,
                urgent_market=urgent_market,
                enforce_takeover_cap=False,
            )
        )
        return msgs

    return msgs


def parse_rules_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
