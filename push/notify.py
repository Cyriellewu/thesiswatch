from __future__ import annotations

"""Outbound alerts — ntfy only unless email is explicitly enabled elsewhere."""

import logging
import os

from push import ntfy_client as _ntfy

logger = logging.getLogger(__name__)

_ALERT_TO_PRIORITY = {
    "urgent": "max",
    "major": "high",
    "attention": "default",
    "routine": "low",
}


def send_alert(level: str, title: str, body: str, *, tags: str | None = None) -> bool:
    prio = _ALERT_TO_PRIORITY.get(level, "default")
    return _ntfy.send_ntfy(title, body, priority=prio, tags=tags)


def ping(title: str = "AlphaWatch", body: str = "ntfy 通路正常 ✅") -> bool:
    return send_alert("routine", title, body, tags="white_check_mark")


def morning_digest_plain(payload: dict) -> str:
    lines = ["📌 AlphaWatch 简报", ""]
    qrows = payload.get("quotes") or []
    if qrows:
        lines.append("持仓行情（快照）:")
        ordered = sorted(
            qrows,
            key=lambda x: abs(float(x.get("chg_pct") or 0.0)),
            reverse=True,
        )
        for row in ordered[:12]:
            sym = row.get("symbol") or ""
            pct = row.get("chg_pct")
            if isinstance(pct, (int, float)):
                lines.append(f"  • {sym}  {float(pct):+.2f}%")
            else:
                lines.append(f"  • {sym}")
        lines.append("")
    macro = (payload.get("macro") or {}).get("strip") or {}
    if macro:
        lines.append(
            "宏观占位: "
            f"VIX {macro.get('vix')} · "
            f"10Y {macro.get('ten_year_yield_pct')}% · "
            f"DXY {macro.get('dxy')}"
        )
    lines.append("")
    lines.append("— 发自本机 AlphaWatch（仅 ntfy）")
    return "\n".join(lines)


def send_morning_digest(payload: dict) -> bool:
    body = morning_digest_plain(payload)
    if not os.environ.get("NTFY_TOPIC", "").strip():
        logger.warning("morning digest skipped: NTFY_TOPIC missing")
        return False
    return _ntfy.send_ntfy("AlphaWatch Morning Digest", body, priority="low", tags="newspaper")


def send_daily_direction_brief(body: str) -> bool:
    """Single concise daily push (ASCII title)."""
    if not os.environ.get("NTFY_TOPIC", "").strip():
        logger.warning("daily direction skipped: NTFY_TOPIC missing")
        return False
    return _ntfy.send_ntfy("AlphaWatch Daily Direction", body, priority="low", tags="sunrise")


def send_news_instinct(*, symbol: str, stance: str, headline: str, severity: str = "important") -> bool:
    """One-line financial instinct push (bullish/bearish)."""
    if not os.environ.get("NTFY_TOPIC", "").strip():
        logger.warning("news instinct skipped: NTFY_TOPIC missing")
        return False
    sym = (symbol or "MARKET").upper()
    stance = (stance or "neutral").lower()
    label = {"bullish": "利好", "bearish": "利空"}.get(stance, stance)
    emoji = "📈" if stance == "bullish" else "📉"
    title = f"{emoji} {sym} · {label}"
    body = headline.strip()
    level = "major" if severity in {"urgent", "important"} else "attention"
    tags = "chart_with_upwards_trend" if stance == "bullish" else "chart_with_downwards_trend"
    return send_alert(level, title, body, tags=tags)


def send_weekly_dca_recommendation(
    *,
    symbol: str,
    amount_usd: float,
    trigger: str,
    chg_pct: float,
    px: float,
) -> bool:
    """Weekly ETF DCA reminder (manual execution)."""
    if not os.environ.get("NTFY_TOPIC", "").strip():
        logger.warning("weekly DCA skipped: NTFY_TOPIC missing")
        return False
    sym = symbol.upper()
    trigger_label = "周内回调，建议切入" if trigger == "dip" else "周五保底，本周勿踏空"
    title = f"定投提醒 · {sym} ${amount_usd:,.0f}"
    body = (
        f"{trigger_label}\n"
        f"现价 ${px:,.2f}（较昨 {chg_pct:+.2f}%）\n"
        f"建议本周定投 ${amount_usd:,.0f} → 请在券商 App 手动下单"
    )
    return send_alert("attention", title, body, tags="money_with_wings")


def send_agent_trade_digest(
    *, account_id: str, agent_label: str, legs: list[str], norm: dict
) -> bool:
    """虚拟账户真实成交后发一条简短推送（不走 alerts 流水线）。"""

    if not os.environ.get("NTFY_TOPIC", "").strip():
        logger.warning("agent trade ntfy skipped: NTFY_TOPIC missing")
        return False

    action = str(norm.get("action") or "")
    aid = (account_id or "").strip() or "account"
    # Title must stay ASCII-only (belt-and-suspenders).
    title = f"Agent trade: {aid} {action}".strip()
    lines: list[str] = []
    if (agent_label or "").strip():
        lines.append(f"账户名: {(agent_label or '').strip()}")
    lines.append(f"做了什么: {', '.join(legs)}")
    try:
        damt = abs(float(norm.get("dollar_amount") or 0))
    except (TypeError, ValueError):
        damt = 0.0
    if damt >= 40:
        lines.append(f"计划金额大约: ${damt:,.0f}")
    lines.append(f"原因: {norm.get('plain_reason', '').strip()}")
    lines.append(f"风险: {norm.get('plain_risk', '').strip()}")
    body = "\n".join(lines)
    return _ntfy.send_ntfy(title, body, priority="default", tags="robot_face")


def send_holdings_sync_ntfy() -> bool:
    """Build message from watchlist (qty + cost) and live yfinance MV."""

    from pathlib import Path

    from data_layer.market_data import fetch_quotes
    from data_layer.portfolio_analytics import load_positions

    root = Path(__file__).resolve().parents[1]
    wl_path = root / "config" / "watchlist.yaml"

    pos = load_positions(wl_path)
    if not pos:
        logger.warning("send_holdings_sync_ntfy: no positions in yaml")
        return False

    syms = [p.ticker for p in pos]
    qmap = {q.symbol.upper(): q for q in (fetch_quotes(syms) or [])}

    mv_pairs: list[tuple[float, str]] = []
    total_mv = 0.0
    for p in pos:
        sym = p.ticker.upper()
        q = qmap.get(sym)
        px_v = float(q.px) if q else 0.0
        chg = float(q.chg_pct) if q else 0.0
        mv = p.qty * px_v
        total_mv += mv
        hdr = f"{p.qty}股 @均价${p.avg_cost_per_share:.2f}"
        mv_pairs.append((mv, f"• {sym}: {hdr} → MV ~${mv:,.2f} · 今 {chg:+.2f}%"))

    mv_pairs.sort(key=lambda x: -x[0])
    lines = [
        "AlphaWatch · 持仓（yfinance 现价）",
        f"组合总市值约 ${total_mv:,.2f}",
        "",
    ]
    lines.extend(s for _, s in mv_pairs)
    lines.append("")
    lines.append("明细以 Streamlit「持仓总览」为准。")
    body = "\n".join(lines)

    if not os.environ.get("NTFY_TOPIC", "").strip():
        logger.warning("holdings ntfy skipped: NTFY_TOPIC missing")
        return False
    return _ntfy.send_ntfy("AlphaWatch Holdings Sync", body, priority="default", tags="chart_with_upwards_trend")
