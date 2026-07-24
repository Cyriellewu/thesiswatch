from __future__ import annotations

"""Rule-based opportunity / holding-discipline alerts.

The stock picker ranks candidates every time prices/news refresh. This module
turns only the most actionable queue changes into SQLite alerts so ntfy can
push them through the existing alert pipeline. It does not place trades.
"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from data_layer import portfolio_analytics as pa
from data_layer.market_data import fetch_quotes
from monitors.auto_watch_config import auto_watch_enabled, load_auto_watch_config, type_enabled
from stock_picker.quality_filter import OpportunityRow, build_opportunity_rows

NY = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(NY).date().isoformat()


def _now_et_iso() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def _insert_alert(
    conn: sqlite3.Connection,
    *,
    level: str,
    category: str,
    symbol: str,
    rule: str,
    title: str,
    what: str,
    why: str,
    relation: str,
    watch: str,
    risk: str,
) -> bool:
    dedupe = f"{category}:{rule}:{symbol}:{_today_et()}"
    cur = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (level, category, symbol, title, what_happened, why_matters,
            relation_to_you, watch_next, risk, dedupe_key, occurred_at, pushed_ntfy)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (level, category, symbol, title, what, why, relation, watch, risk, dedupe, _now_et_iso()),
    )
    return cur.rowcount > 0


def _held_map() -> dict[str, dict[str, float]]:
    positions = pa.load_positions()
    if not positions:
        return {}
    qmap = {q.symbol.upper(): q for q in fetch_quotes([p.ticker for p in positions])}
    mv_by: dict[str, float] = {}
    total = 0.0
    for p in positions:
        sym = p.ticker.upper()
        quote = qmap.get(sym)
        px = float(quote.px) if quote else float(p.avg_cost_per_share)
        mv = float(p.qty) * px
        mv_by[sym] = mv
        total += mv
    out: dict[str, dict[str, float]] = {}
    for p in positions:
        sym = p.ticker.upper()
        mv = mv_by.get(sym, 0.0)
        cost = float(p.qty) * float(p.avg_cost_per_share)
        out[sym] = {
            "avg_cost": float(p.avg_cost_per_share),
            "weight": (mv / total * 100.0) if total > 0 else 0.0,
            "return_pct": ((mv - cost) / cost * 100.0) if cost > 0 else 0.0,
        }
    return out


def _holding_action(row: OpportunityRow, held: dict[str, float]) -> str:
    ret = float(held.get("return_pct") or 0.0)
    weight = float(held.get("weight") or 0.0)
    avg_cost = float(held.get("avg_cost") or 0.0)
    if weight >= 15 or (row.bucket == "高波动观察" and weight >= 8):
        return "减仓评估"
    if ret >= 25:
        return "保护利润"
    if ret <= -5 or (avg_cost > 0 and row.price < avg_cost) or (row.ma50 > 0 and row.price < row.ma50):
        return "检查逻辑"
    return "继续持有"


def emit_opportunity_alerts(conn: sqlite3.Connection, *, max_alerts: int = 8) -> int:
    if not auto_watch_enabled():
        return 0
    cfg = load_auto_watch_config()
    max_alerts = int((cfg.get("limits") or {}).get("max_auto_alerts_per_cycle") or max_alerts)
    rows = build_opportunity_rows()
    held = _held_map()
    inserted = 0

    unheld = [r for r in rows if r.symbol not in held]
    ready = [r for r in unheld if str(getattr(r, "current_action", "")) in {"priority_buyable", "buyable"}]
    wait = [r for r in unheld if r.decision_queue == "好票等回踩" and (r.news_score >= 3 or r.c_score >= 5)]

    if type_enabled("entry_range"):
        ready_rows = ready[:4]
    else:
        ready_rows = []
    for row in ready_rows:
        low_allocated = "低配" in row.bucket_gap_note
        level = "urgent" if low_allocated and row.q_score >= 7 else "major" if row.q_score >= 7 else "attention"
        inserted += int(
            _insert_alert(
                conn,
                level=level,
                category="opportunity",
                symbol=row.symbol,
                rule="entered_buy_zone",
                title=f"AlphaWatch: {row.symbol} 已进入可分批区",
                what=(
                    f"{row.symbol} 当前约 ${row.price:,.2f}，位于{row.p_status}；"
                    f"推荐区间 ${row.zones.recommended_low:,.2f}-${row.zones.recommended_high:,.2f}。"
                ),
                why=f"类型：{row.bucket} / {row.label}。Q {row.q_score}/10、T {row.t_score}/10，{row.bucket_gap_note}。{getattr(row, 'buyable_reason', '')}",
                relation="这是未持有/补仓逻辑，只代表可小额分批观察，不代表必须买。",
                watch=f"可以小额分批；如果想更保守，可等更舒服回踩位 ${row.zones.comfortable_buy:,.2f}。",
                risk="不要一次性重仓。若市场情绪刹车亮起，优先等回踩。",
            )
        )
        if inserted >= max_alerts:
            return inserted

    for row in wait[:2]:
        inserted += int(
            _insert_alert(
                conn,
                level="routine",
                category="opportunity",
                symbol=row.symbol,
                rule="wait-pullback",
                title=f"好票等回踩：{row.symbol} 新闻/催化增强但不追高",
                what=f"{row.symbol} 属于{row.label}，但当前为{row.p_status}，不适合追。",
                why=f"News {row.news_score:+d}、C {row.c_score}/10；有催化时更要用价格纪律过滤情绪。",
                relation="适合设回踩提醒，不适合把新闻利好直接翻译成买入。",
                watch=f"回到 ${row.zones.recommended_low:,.2f}-${row.zones.recommended_high:,.2f} 再看。",
                risk="利好兑现后也可能回撤，提醒只负责让你看一眼。",
            )
        )
        if inserted >= max_alerts:
            return inserted

    held_rows = [r for r in rows if r.symbol in held] if type_enabled("holding_drawdown") else []
    for row in held_rows:
        action = _holding_action(row, held[row.symbol])
        if action == "继续持有":
            continue
        level = "major" if action == "减仓评估" else "attention"
        inserted += int(
            _insert_alert(
                conn,
                level=level,
                category="holding_discipline",
                symbol=row.symbol,
                rule=action,
                title=f"持仓纪律：{row.symbol} {action}",
                what=(
                    f"{row.symbol} 当前 ${row.price:,.2f}，浮盈 {held[row.symbol]['return_pct']:+.1f}%，"
                    f"仓位 {held[row.symbol]['weight']:.1f}%。"
                ),
                why="已持有股票不按买入区间判断卖出，而按仓位、成本、回撤、均线和新闻变化检查。",
                relation=f"对你的持仓是“{action}”提醒，不是强制交易。",
                watch="优先设置回撤/成本/均线提醒；只有基本面逻辑变坏时才考虑动作。",
                risk="保护利润和检查逻辑都不是立刻卖出，它们只是防止情绪化忽视风险。",
            )
        )
        if inserted >= max_alerts:
            break

    return inserted
