from __future__ import annotations

"""Practical buy zones for the lazy watchlist flow.

These zones are intentionally simple and useful:
- buy_now: a small range around the current price
- comfortable: near the 50-day moving average
- deep_value: near the 200-day moving average
"""

import os
from dataclasses import dataclass

from data_layer.market_data import fetch_quotes


@dataclass(frozen=True)
class PracticalZones:
    ticker: str
    current_price: float
    buy_now_low: float
    buy_now_high: float
    comfortable_price: float
    comfortable_discount_pct: float
    deep_value_price: float
    deep_value_discount_pct: float
    avoid_above_price: float
    ma50: float
    ma200: float
    buy_mode: str = "extra_add"
    status_label: str = "当前可分批"


def build_price_ladder(
    *,
    current_price: float,
    recommended_low: float,
    recommended_high: float,
    ma200: float | None = None,
    low_6m: float | None = None,
) -> dict[str, float]:
    """Build a semantically safe buy/sell-side ladder.

    Buy-side levels must be below current price. High-side levels must be above
    current price. This is intentionally strict because labels like "跌一点" and
    "大跌" are direction-sensitive.
    """

    px = max(0.01, float(current_price))
    rec_low = max(0.01, float(recommended_low))
    rec_high = max(0.01, float(recommended_high))

    if rec_low > rec_high:
        mid = (rec_low + rec_high) / 2.0
        rec_low = mid * 0.97
        rec_high = mid * 1.03

    pullback_buy = min(
        px * 0.97,
        rec_low + (px - rec_low) * 0.35,
    )
    if pullback_buy >= px:
        pullback_buy = px * 0.97

    candidates = [
        px * 0.90,
        rec_low * 0.95,
    ]
    if ma200 and ma200 < px:
        candidates.append(float(ma200))
    if low_6m:
        candidates.append(float(low_6m) * 1.03)
    deep_buy = min(candidates)
    if deep_buy >= pullback_buy:
        deep_buy = pullback_buy * 0.95

    avoid_above = max(
        rec_high * 1.05,
        px * 1.05,
    )
    if avoid_above <= px:
        avoid_above = px * 1.05

    # Last-resort guard. Do not let an invalid ladder reach the UI.
    if not (deep_buy < pullback_buy < px < avoid_above):
        pullback_buy = min(pullback_buy, px * 0.97)
        deep_buy = min(deep_buy, pullback_buy * 0.95, px * 0.90)
        avoid_above = max(avoid_above, px * 1.05)

    return {
        "recommended_low": round(rec_low, 2),
        "recommended_high": round(rec_high, 2),
        "pullback_buy": round(pullback_buy, 2),
        "deep_buy": round(deep_buy, 2),
        "avoid_above": round(avoid_above, 2),
    }


def _round_money(v: float) -> float:
    return round(max(0.01, float(v or 0.01)), 2)


def _fallback_price(ticker: str) -> float:
    q = fetch_quotes([ticker], ttl_seconds=60)
    return float(q[0].px) if q else 0.0


def _history_mas(ticker: str) -> tuple[float, float]:
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return 0.0, 0.0
    try:
        import yfinance as yf  # noqa: PLC0415

        hist = yf.Ticker(ticker).history(period="260d", interval="1d", auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist:
            return 0.0, 0.0
        close = hist["Close"].dropna()
        if close.empty:
            return 0.0, 0.0
        ma50 = float(close.tail(min(50, len(close))).mean())
        ma200 = float(close.tail(min(200, len(close))).mean())
        return ma50, ma200
    except Exception:
        return 0.0, 0.0


def calc_practical_zones(
    ticker: str,
    *,
    price: float | None = None,
    ma50: float | None = None,
    ma200: float | None = None,
) -> PracticalZones:
    """Return three useful buy levels for a followed ticker.

    This deliberately avoids unreachable "perfect valuation" targets. If live
    moving averages are missing, it falls back to simple current-price discounts
    so the UI and sentinels keep working.
    """

    symbol = ticker.strip().upper()
    px = float(price or 0.0) or _fallback_price(symbol)
    px = max(0.01, px)

    hist_ma50, hist_ma200 = (0.0, 0.0)
    if not ma50 or not ma200:
        hist_ma50, hist_ma200 = _history_mas(symbol)
    m50 = float(ma50 or hist_ma50 or px * 0.95)
    m200 = float(ma200 or hist_ma200 or px * 0.88)

    is_core_etf = symbol in {"VOO", "VTI", "SPY", "IVV", "QQQ", "QQQM", "SCHD"}
    is_tech_etf = symbol in {"QQQ", "QQQM", "XLK"}

    if is_core_etf and m50 > 0 and m200 > 0:
        # ETF has two meanings:
        # 1) regular DCA can continue almost anytime;
        # 2) extra-add zone requires a real pullback toward trend support.
        anchor = 0.65 * m50 + 0.35 * m200
        rec_low = anchor * (0.94 if is_tech_etf else 0.96)
        rec_high = m50 * (1.04 if is_tech_etf else 1.03)
        if px > rec_high:
            buy_mode = "dca_only" if is_tech_etf else "wait_pullback"
            status_label = "可定投，不急加仓" if is_tech_etf else "等回额外加仓区"
        elif rec_low <= px <= rec_high:
            buy_mode = "extra_add"
            status_label = "进入额外加仓区"
        else:
            buy_mode = "deep_add"
            status_label = "深度回调，可重点分批"
    else:
        rec_low = px * 0.95
        rec_high = px * 1.05
        buy_mode = "extra_add"
        status_label = "当前可分批"

    ladder = build_price_ladder(
        current_price=px,
        recommended_low=rec_low,
        recommended_high=rec_high,
        ma200=m200,
    )
    buy_now_low = ladder["recommended_low"]
    buy_now_high = ladder["recommended_high"]
    pullback_buy = ladder["pullback_buy"]
    deep_value = ladder["deep_buy"]
    avoid_above = ladder["avoid_above"]

    comfortable_discount = (pullback_buy - px) / px * 100.0
    deep_discount = (deep_value - px) / px * 100.0

    return PracticalZones(
        ticker=symbol,
        current_price=_round_money(px),
        buy_now_low=_round_money(buy_now_low),
        buy_now_high=_round_money(buy_now_high),
        comfortable_price=_round_money(pullback_buy),
        comfortable_discount_pct=round(comfortable_discount, 1),
        deep_value_price=_round_money(deep_value),
        deep_value_discount_pct=round(deep_discount, 1),
        avoid_above_price=_round_money(avoid_above),
        buy_mode=buy_mode,
        status_label=status_label,
        ma50=_round_money(m50),
        ma200=_round_money(m200),
    )


def is_in_buy_zone(price: float, zones: PracticalZones) -> bool:
    return zones.buy_now_low <= float(price) <= zones.buy_now_high
