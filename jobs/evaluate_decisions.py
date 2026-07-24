from __future__ import annotations

import json
from dataclasses import dataclass

from data_layer.market_data import fetch_quotes
from db.client import bootstrap_database, get_conn


@dataclass
class EvalStats:
    scanned: int = 0
    inserted: int = 0
    skipped_no_trade: int = 0
    updated_effectiveness: int = 0


def _json_id_list(blob: object) -> list[int]:
    if blob is None:
        return []
    if isinstance(blob, list):
        raw = blob
    else:
        s = str(blob).strip()
        if not s:
            return []
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def evaluate_decisions_1d() -> EvalStats:
    st = EvalStats()
    conn = get_conn(read_only=False)
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.account_id, d.action, d.symbol, d.source_signal_ids
            FROM agent_decisions d
            LEFT JOIN decision_outcomes o
              ON o.decision_id = d.id AND o.eval_horizon = '1d'
            WHERE o.id IS NULL
              AND COALESCE(
                    datetime(NULLIF(d.ts, '')),
                    datetime(replace(substr(NULLIF(d.ts, ''), 1, 19), 'T', ' ')),
                    datetime(d.created_at)
                  ) <= datetime('now', '-1 day')
            ORDER BY d.id DESC
            LIMIT 300
            """
        ).fetchall()
        st.scanned = len(rows)
        if not rows:
            conn.commit()
            return st

        ids = [int(r["id"]) for r in rows]
        ph = ",".join(["?"] * len(ids))
        trows = conn.execute(
            f"""
            SELECT decision_id, symbol,
                   SUM(CASE WHEN upper(side)='BUY' THEN qty ELSE 0 END) AS buy_qty,
                   SUM(CASE WHEN upper(side)='BUY' THEN qty * px ELSE 0 END) AS buy_cost
            FROM agent_trades
            WHERE decision_id IN ({ph})
            GROUP BY decision_id, symbol
            """,
            tuple(ids),
        ).fetchall()
        by_dec: dict[int, dict] = {}
        syms: set[str] = set()
        for tr in trows:
            did = int(tr["decision_id"])
            buy_qty = float(tr["buy_qty"] or 0.0)
            if buy_qty <= 0:
                continue
            buy_cost = float(tr["buy_cost"] or 0.0)
            sym = str(tr["symbol"]).upper()
            by_dec[did] = {"symbol": sym, "entry_px": buy_cost / buy_qty}
            syms.add(sym)

        px_map = {q.symbol.upper(): float(q.px) for q in fetch_quotes(sorted(syms))} if syms else {}

        for r in rows:
            did = int(r["id"])
            trade = by_dec.get(did)
            if not trade:
                st.skipped_no_trade += 1
                continue
            px_now = float(px_map.get(trade["symbol"]) or 0.0)
            if px_now <= 0:
                continue
            ret = (px_now - trade["entry_px"]) / trade["entry_px"] * 100.0
            if ret >= 2.0:
                outcome = "win"
            elif ret <= -2.0:
                outcome = "loss"
            else:
                outcome = "breakeven"
            conn.execute(
                """
                INSERT OR IGNORE INTO decision_outcomes
                (decision_id, eval_horizon, px_ref, return_pct, outcome, details_json)
                VALUES (?, '1d', ?, ?, ?, ?)
                """,
                (
                    did,
                    round(px_now, 6),
                    round(ret, 4),
                    outcome,
                    json.dumps(
                        {"entry_px": round(trade["entry_px"], 6), "symbol": trade["symbol"]},
                        ensure_ascii=False,
                    ),
                ),
            )
            st.inserted += 1

            sig_ids = sorted(set(_json_id_list(r["source_signal_ids"])))
            for sid in sig_ids:
                srow = conn.execute(
                    "SELECT signal_type FROM market_signals WHERE id = ?",
                    (sid,),
                ).fetchone()
                if not srow:
                    continue
                s_type = str(srow["signal_type"])
                cur = conn.execute(
                    "SELECT total,wins,losses,breakeven,avg_return_pct FROM signal_effectiveness WHERE signal_type = ?",
                    (s_type,),
                ).fetchone()
                if cur:
                    total = int(cur["total"]) + 1
                    wins = int(cur["wins"]) + (1 if outcome == "win" else 0)
                    losses = int(cur["losses"]) + (1 if outcome == "loss" else 0)
                    be = int(cur["breakeven"]) + (1 if outcome == "breakeven" else 0)
                    old_avg = float(cur["avg_return_pct"] or 0.0)
                    avg = ((old_avg * (total - 1)) + ret) / total
                    conn.execute(
                        """
                        UPDATE signal_effectiveness
                        SET total=?, wins=?, losses=?, breakeven=?, avg_return_pct=?, updated_at=datetime('now')
                        WHERE signal_type=?
                        """,
                        (total, wins, losses, be, round(avg, 4), s_type),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO signal_effectiveness
                        (signal_type,total,wins,losses,breakeven,avg_return_pct,updated_at)
                        VALUES (?,?,?,?,?,?,datetime('now'))
                        """,
                        (
                            s_type,
                            1,
                            1 if outcome == "win" else 0,
                            1 if outcome == "loss" else 0,
                            1 if outcome == "breakeven" else 0,
                            round(ret, 4),
                        ),
                    )
                st.updated_effectiveness += 1

        conn.commit()
        return st
    finally:
        conn.close()


if __name__ == "__main__":
    bootstrap_database()
    stats = evaluate_decisions_1d()
    print(vars(stats))
