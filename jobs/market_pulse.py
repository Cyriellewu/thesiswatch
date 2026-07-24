"""统一市场脉冲：抓一次 → 落库 → 信号 → `alerts` →（可选）ntfy；供虚拟账户权益快照共用。"""

from __future__ import annotations

import sqlite3
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from data_layer.persist import persist_headlines, persist_quotes  # noqa: E402
from data_layer.universe import load_watchlist_tickers, load_opportunity_tickers, pulse_symbol_universe  # noqa: E402
from monitors.holding_alert_rules import emit_holding_move_alerts  # noqa: E402
from monitors.opportunity_alert_rules import emit_opportunity_alerts  # noqa: E402
from monitors.market_radar import emit_market_radar_alerts  # noqa: E402
from monitors.auto_watch_config import auto_watch_enabled, type_enabled  # noqa: E402
from monitors.watchlist_sentinels import scan_watchlist_sentinels  # noqa: E402
from monitors import holdings_monitor  # noqa: E402
from monitors.macro_sentinel import run_macro_sentinel  # noqa: E402
from push.alert_pipeline import flush_pending_ntfy, materialize_signals_batch  # noqa: E402
from signals.fusion import fuse_recent_signals, persist_bundle_signals  # noqa: E402
from signals.opportunity_sync import sync_opportunity_scores_with_quotes  # noqa: E402
from simulation.equity_snapshots import snapshot_all_agent_accounts  # noqa: E402
from signals.pulse import (  # noqa: E402
    emit_company_dynamic_signals,
    emit_macro_signal,
    emit_news_signals,
    emit_price_signals,
    emit_technical_signals,
)


def run_market_pulse(
    *,
    conn: sqlite3.Connection,
    push_ntfy: bool = False,
    ntfy_limit: int = 5,
    run_agents: bool = True,
    force_agent_review: bool = False,
) -> dict:
    """
    需要外部注入已打开的数据库连接（便于与 Streamlit 共用同一文件）。

    返回统计信息，便于 UI 展示。
    """

    syms = pulse_symbol_universe()
    hold = set(load_watchlist_tickers())
    opp = set(load_opportunity_tickers()) - hold

    payload = holdings_monitor.run_holdings_pulse(syms)
    quotes = payload.get("quotes") or []
    headlines = payload.get("headlines_min") or []

    from data_layer.market_data import Quote  # noqa: PLC0415

    qobjs = [Quote(**row) if isinstance(row, dict) else row for row in quotes]
    persist_quotes(conn, qobjs)
    news_new = persist_headlines(conn, headlines)
    if auto_watch_enabled() and type_enabled("holding_drawdown"):
        holding_move_alerts = emit_holding_move_alerts(conn, quotes=qobjs)
    else:
        holding_move_alerts = 0
    opportunity_alerts = emit_opportunity_alerts(conn)
    market_radar_alerts = emit_market_radar_alerts(conn)
    watchlist_sentinel_alerts = scan_watchlist_sentinels(conn)

    macro = run_macro_sentinel()
    macro_strip = (macro.get("strip") or {}) if isinstance(macro, dict) else {}
    mid = emit_macro_signal(conn, macro_strip)

    price_ids = emit_price_signals(conn, quotes=qobjs, holding_syms=hold, opportunity_syms=opp)
    news_ids = emit_news_signals(conn, headlines=headlines, holding_syms=hold)
    tech_ids = (
        []
        if os.environ.get("ALPHAWATCH_OFFLINE") == "1"
        else emit_technical_signals(conn, symbols=sorted(hold | opp))
    )
    dynamic_ids = emit_company_dynamic_signals(conn, headlines=headlines, holding_syms=hold)
    bundles = fuse_recent_signals(conn, lookback_hours=24 * 7, top_n=20)
    bundle_ids = persist_bundle_signals(conn, bundles, min_score=30)

    to_materialize = [*price_ids, *news_ids, *tech_ids, *dynamic_ids, *bundle_ids, mid]
    alerts_new = materialize_signals_batch(conn, to_materialize)
    opp_rescored = sync_opportunity_scores_with_quotes(conn, qobjs)

    px_map = {str(q.symbol).upper(): float(q.px) for q in qobjs}

    price_alarms_triggered = 0
    if push_ntfy:
        try:
            from tasks.price_alarms import check_alarms  # noqa: PLC0415

            price_alarms_triggered = len(check_alarms(px_map, send=True))
        except Exception:
            price_alarms_triggered = 0

    pushed = 0
    if push_ntfy:
        pushed = flush_pending_ntfy(conn, limit=ntfy_limit)

    agent_stats: dict = {}
    if run_agents:
        from agents.orchestrator import run_agent_cycle

        agent_stats = run_agent_cycle(
            conn,
            px_map,
            push_trade_ntfy=push_ntfy,
            force_review=force_agent_review,
        )

    snap_n = snapshot_all_agent_accounts(conn, px_map)

    return {
        "symbols": len(syms),
        "quotes": len(qobjs),
        "news_rows_new": news_new,
        "signals": len(to_materialize),
        "signal_breakdown": {
            "price": len(price_ids),
            "news": len(news_ids),
            "technical": len(tech_ids),
            "dynamic": len(dynamic_ids),
            "bundles": len(bundle_ids),
            "macro": 1,
        },
        "alerts_materialized": alerts_new,
        "holding_move_alerts": holding_move_alerts,
        "opportunity_alerts": opportunity_alerts,
        "market_radar_alerts": market_radar_alerts,
        "watchlist_sentinel_alerts": watchlist_sentinel_alerts,
        "opportunity_rescored": opp_rescored,
        "equity_snapshots": snap_n,
        "ntfy_pushed": pushed,
        "price_alarms_triggered": price_alarms_triggered,
        "agent_cycle": agent_stats,
    }


if __name__ == "__main__":
    from db.client import bootstrap_database, get_conn

    bootstrap_database()
    c = get_conn()
    try:
        out = run_market_pulse(conn=c, push_ntfy=True)
        c.commit()
        print(out)
    finally:
        c.close()
