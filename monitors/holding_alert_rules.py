"""Holding-level abnormal move alerts.

This module turns portfolio price movement into human-readable rows in
``alerts``. It is intentionally rule-based: no LLM, no broker actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import sqlite3
from zoneinfo import ZoneInfo

from data_layer.market_data import Quote
from data_layer.portfolio_analytics import PositionRow, load_positions

NY = ZoneInfo("America/New_York")


ROLE_THRESHOLDS = {
    "ETF": {"daily_move_pct": 3.0, "intraday_range_pct": 4.0},
    "core_growth": {"daily_move_pct": 5.0, "intraday_range_pct": 6.0},
    "satellite_high_vol": {"daily_move_pct": 8.0, "intraday_range_pct": 10.0},
}

DEFAULT_ROLES = {
    "VOO": "ETF",
    "VTI": "ETF",
    "SPY": "ETF",
    "IVV": "ETF",
    "QQQ": "ETF",
    "QQQM": "ETF",
    "MSFT": "core_growth",
    "GOOGL": "core_growth",
    "META": "core_growth",
    "NVDA": "core_growth",
    "AVGO": "core_growth",
    "PANW": "core_growth",
    "TSLA": "satellite_high_vol",
    "IREN": "satellite_high_vol",
    "OKLO": "satellite_high_vol",
    "SNDK": "satellite_high_vol",
}


@dataclass(frozen=True)
class IntradayStats:
    day_high: float | None
    day_low: float | None
    previous_close: float | None
    intraday_range_pct: float | None
    avg_intraday_range_20d: float | None
    abnormal_range_ratio: float | None


@dataclass(frozen=True)
class HoldingMove:
    symbol: str
    role: str
    price: float
    day_change_pct: float
    daily_pnl_usd: float
    position_weight_pct: float
    stats: IntradayStats


def _today_et() -> str:
    return datetime.now(NY).date().isoformat()


def _now_et_iso() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def _role_for_symbol(symbol: str) -> str:
    env_key = f"ALPHA_HOLDING_ROLE_{symbol.upper()}"
    env_val = os.environ.get(env_key, "").strip()
    if env_val in ROLE_THRESHOLDS:
        return env_val
    return DEFAULT_ROLES.get(symbol.upper(), "core_growth")


def _level_for_ratio(ratio: float) -> str:
    if ratio >= 1.5:
        return "urgent"
    if ratio >= 1.0:
        return "major"
    return "attention"


def _fallback_previous_close(price: float, day_change_pct: float) -> float | None:
    denom = 1.0 + day_change_pct / 100.0
    if price <= 0 or denom <= 0:
        return None
    return price / denom


def _intraday_stats(symbol: str) -> IntradayStats:
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return IntradayStats(None, None, None, None, None, None)

    try:
        import yfinance as yf  # noqa: PLC0415

        hist = yf.Ticker(symbol).history(period="35d", interval="1d", auto_adjust=True)
    except Exception:
        return IntradayStats(None, None, None, None, None, None)

    if hist is None or hist.empty or len(hist.index) < 2:
        return IntradayStats(None, None, None, None, None, None)

    high = hist["High"].astype(float)
    low = hist["Low"].astype(float)
    close = hist["Close"].astype(float)

    day_high = float(high.iloc[-1])
    day_low = float(low.iloc[-1])
    previous_close = float(close.iloc[-2])
    if previous_close <= 0:
        return IntradayStats(day_high, day_low, None, None, None, None)

    intraday_range_pct = (day_high - day_low) / previous_close * 100.0

    ranges = []
    # Use completed prior sessions for the baseline, so today's spike does not dilute itself.
    for i in range(max(1, len(close) - 21), len(close) - 1):
        prev = float(close.iloc[i - 1])
        if prev > 0:
            ranges.append((float(high.iloc[i]) - float(low.iloc[i])) / prev * 100.0)
    avg_range = sum(ranges) / len(ranges) if ranges else None
    ratio = intraday_range_pct / avg_range if avg_range and avg_range > 0 else None

    return IntradayStats(
        day_high=day_high,
        day_low=day_low,
        previous_close=previous_close,
        intraday_range_pct=intraday_range_pct,
        avg_intraday_range_20d=avg_range,
        abnormal_range_ratio=ratio,
    )


def _build_moves(positions: list[PositionRow], quotes: list[Quote]) -> list[HoldingMove]:
    qmap = {q.symbol.upper(): q for q in quotes}
    priced = []
    for p in positions:
        q = qmap.get(p.ticker.upper())
        if not q or float(q.px or 0.0) <= 0:
            continue
        priced.append((p, q))

    total_mv = sum(float(p.qty) * float(q.px) for p, q in priced)
    if total_mv <= 0:
        return []

    out: list[HoldingMove] = []
    for p, q in priced:
        sym = p.ticker.upper()
        px = float(q.px)
        chg = float(q.chg_pct or 0.0)
        stats = _intraday_stats(sym)
        prev_close = stats.previous_close or _fallback_previous_close(px, chg)
        daily_pnl = float(p.qty) * (px - prev_close) if prev_close else 0.0
        mv = float(p.qty) * px
        out.append(
            HoldingMove(
                symbol=sym,
                role=_role_for_symbol(sym),
                price=px,
                day_change_pct=chg,
                daily_pnl_usd=daily_pnl,
                position_weight_pct=mv / total_mv * 100.0,
                stats=stats,
            )
        )
    return out


def _insert_alert(
    conn: sqlite3.Connection,
    *,
    level: str,
    symbol: str,
    rule: str,
    title: str,
    what: str,
    why: str,
    watch: str,
    risk: str,
) -> bool:
    dedupe = f"holding-move:{rule}:{symbol}:{_today_et()}"
    cur = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (level, category, symbol, title, what_happened, why_matters,
            relation_to_you, watch_next, risk, dedupe_key, occurred_at, pushed_ntfy)
           VALUES (?, 'holding_move', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            level,
            symbol,
            title,
            what,
            why,
            f"你持有 {symbol}，这会直接影响虚拟/实仓观察。",
            watch,
            risk,
            dedupe,
            _now_et_iso(),
        ),
    )
    return cur.rowcount > 0


def emit_holding_move_alerts(
    conn: sqlite3.Connection,
    *,
    quotes: list[Quote],
    positions: list[PositionRow] | None = None,
) -> int:
    """Evaluate holding move rules and insert rows into ``alerts``.

    Returns the number of newly inserted alert rows.
    """

    positions = positions if positions is not None else load_positions()
    moves = _build_moves(positions, quotes)
    if not moves:
        return 0

    inserted = 0
    biggest_gain = max(moves, key=lambda m: m.daily_pnl_usd)
    biggest_loss = min(moves, key=lambda m: m.daily_pnl_usd)

    for m in moves:
        th = ROLE_THRESHOLDS.get(m.role, ROLE_THRESHOLDS["core_growth"])
        daily_th = float(th["daily_move_pct"])
        range_th = float(th["intraday_range_pct"])

        a = abs(m.day_change_pct)
        if a >= daily_th:
            ratio = a / daily_th if daily_th else 1.0
            direction = "上涨" if m.day_change_pct > 0 else "下跌"
            inserted += int(
                _insert_alert(
                    conn,
                    level=_level_for_ratio(ratio),
                    symbol=m.symbol,
                    rule="daily",
                    title=f"持仓大波动：{m.symbol} 单日{direction} {m.day_change_pct:+.2f}%",
                    what=f"{m.symbol} 当前约 ${m.price:,.2f}，单日{direction} {m.day_change_pct:+.2f}%，超过 {m.role} 阈值 {daily_th:.0f}%。",
                    why=f"这个幅度已经足够影响组合判断；今日估算贡献约 ${m.daily_pnl_usd:+,.2f}，仓位约 {m.position_weight_pct:.1f}%。",
                    watch="先看是否有财报、监管、评级、行业消息或大盘共振，再决定是否处理仓位。",
                    risk="急涨也可能回撤，急跌也可能是假摔；提醒只负责叫醒你，不代表买卖。",
                )
            )

        intraday = m.stats.intraday_range_pct
        if intraday is not None and intraday >= range_th:
            ratio = intraday / range_th if range_th else 1.0
            inserted += int(
                _insert_alert(
                    conn,
                    level=_level_for_ratio(ratio),
                    symbol=m.symbol,
                    rule="intraday-range",
                    title=f"持仓振幅异常：{m.symbol} 日内振幅 {intraday:.2f}%",
                    what=f"{m.symbol} 今日高低点振幅约 {intraday:.2f}%，超过 {m.role} 阈值 {range_th:.0f}%。",
                    why="有些票收盘涨跌不大，但盘中已经大起大落；这种情况容易隐藏真实风险。",
                    watch="查看盘中波动对应的新闻时间点，尤其是财报、会议、监管或行业消息。",
                    risk="日内振幅大说明分歧变大，不适合情绪化追涨杀跌。",
                )
            )

        ratio = m.stats.abnormal_range_ratio
        if ratio is not None and ratio >= 1.8:
            avg = m.stats.avg_intraday_range_20d or 0.0
            inserted += int(
                _insert_alert(
                    conn,
                    level=_level_for_ratio(ratio / 1.8),
                    symbol=m.symbol,
                    rule="abnormal-range",
                    title=f"持仓相对异常：{m.symbol} 振幅是平时 {ratio:.1f} 倍",
                    what=f"{m.symbol} 今日振幅约 {m.stats.intraday_range_pct:.2f}%，过去 20 日平均约 {avg:.2f}%。",
                    why="这不是单纯绝对波动大，而是相对它自己的平时状态明显异常。",
                    watch="优先确认是不是新消息驱动；如果没有消息，可能是资金或情绪波动。",
                    risk="相对异常会提高短线噪声，仓位大的票尤其要看一眼。",
                )
            )

    for m, rule, label in (
        (biggest_gain, "top-contributor", "最大贡献"),
        (biggest_loss, "top-dragger", "最大拖累"),
    ):
        if m.position_weight_pct < 15:
            continue
        if abs(m.daily_pnl_usd) < 10 and abs(m.day_change_pct) < 1.0:
            continue
        inserted += int(
            _insert_alert(
                conn,
                level="attention",
                symbol=m.symbol,
                rule=rule,
                title=f"组合{label}：{m.symbol} ${m.daily_pnl_usd:+,.2f}",
                what=f"{m.symbol} 是本轮持仓的{label}，今日估算 P&L 约 ${m.daily_pnl_usd:+,.2f}，仓位约 {m.position_weight_pct:.1f}%。",
                why="仓位较大的票即使涨跌幅不夸张，也可能主导你当天账户体验。",
                watch="如果连续多天成为最大拖累/贡献，就该复盘仓位是否还符合你的风险偏好。",
                risk="这是组合归因提醒，不是单独的买卖建议。",
            )
        )

    return inserted
