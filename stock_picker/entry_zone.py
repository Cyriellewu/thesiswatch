from __future__ import annotations

from dataclasses import dataclass

from stock_picker.practical_zones import build_price_ladder


@dataclass
class EntryZones:
    current_px: float
    deep_value: float
    recommended_low: float
    recommended_high: float
    avoid_above: float
    comfortable_buy: float = 0.0
    status: str = "等回推荐区间"
    stock_type: str = "steady_growth"
    trend_warning: bool = False


@dataclass
class ExitZones:
    trim_watch: float
    pullback_risk: float
    trend_break: float


def calc_entry_zones(*, current_px: float, low_3m: float, low_6m: float, ma50: float, ma200: float) -> EntryZones:
    px = max(0.01, float(current_px))
    l3 = max(0.01, float(low_3m or px))
    l6 = max(0.01, float(low_6m or l3))
    m50 = max(0.01, float(ma50 or px))
    m200 = max(0.01, float(ma200 or m50))

    fair_ref = (m50 * 0.6) + (m200 * 0.4)
    deep = min(l6, fair_ref * 0.9)
    rec_low = max(l3, fair_ref * 0.95)
    rec_high = min(px, fair_ref * 1.05)
    avoid = fair_ref * 1.2

    if rec_low > rec_high:
        rec_low = min(rec_low, px * 0.98)
        rec_high = max(rec_low, px)

    return EntryZones(
        current_px=round(px, 2),
        deep_value=round(deep, 2),
        recommended_low=round(rec_low, 2),
        recommended_high=round(rec_high, 2),
        avoid_above=round(avoid, 2),
        comfortable_buy=round(min(rec_low, px * 0.97), 2),
    )


PULLBACK_CONFIG: dict[str, dict[str, float]] = {
    "etf_core": {"comfortable_pct": 0.03, "deep_pct": 0.08, "avoid_pct": 0.08, "base_discount": 0.02},
    "defensive_quality": {"comfortable_pct": 0.04, "deep_pct": 0.10, "avoid_pct": 0.07, "base_discount": 0.03},
    "steady_growth": {"comfortable_pct": 0.06, "deep_pct": 0.14, "avoid_pct": 0.10, "base_discount": 0.05},
    "ai_growth": {"comfortable_pct": 0.08, "deep_pct": 0.18, "avoid_pct": 0.12, "base_discount": 0.07},
    "revaluation_trend": {"comfortable_pct": 0.10, "deep_pct": 0.22, "avoid_pct": 0.15, "base_discount": 0.10},
    "high_vol_watch": {"comfortable_pct": 0.12, "deep_pct": 0.28, "avoid_pct": 0.18, "base_discount": 0.12},
}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def validate_price_ladder(
    *,
    current_price: float,
    recommended_low: float,
    recommended_high: float,
    comfortable_buy: float,
    deep_value: float,
    avoid_above: float,
) -> dict[str, float]:
    px = max(0.01, float(current_price))
    rec_low = max(0.01, float(recommended_low))
    rec_high = max(0.01, float(recommended_high))
    comfortable = max(0.01, float(comfortable_buy))
    deep = max(0.01, float(deep_value))
    avoid = max(0.01, float(avoid_above))

    ladder = build_price_ladder(
        current_price=px,
        recommended_low=rec_low,
        recommended_high=rec_high,
    )
    rec_low = ladder["recommended_low"]
    rec_high = ladder["recommended_high"]
    comfortable = min(comfortable, ladder["pullback_buy"])
    deep = min(deep, ladder["deep_buy"])
    avoid = max(avoid, ladder["avoid_above"])

    if comfortable >= px:
        comfortable = ladder["pullback_buy"]
    if deep >= comfortable:
        deep = min(ladder["deep_buy"], comfortable * 0.95)
    if avoid <= px:
        avoid = ladder["avoid_above"]

    return {
        "recommended_low": round(rec_low, 2),
        "recommended_high": round(rec_high, 2),
        "comfortable_buy": round(comfortable, 2),
        "deep_value": round(deep, 2),
        "avoid_above": round(avoid, 2),
    }


def calc_typed_entry_zones(
    *,
    current_px: float,
    low_3m: float,
    low_6m: float,
    high_3m: float,
    high_52w: float,
    ma50: float,
    ma100: float | None = None,
    ma200: float | None = None,
    annualized_volatility: float = 0.25,
    stock_type: str = "steady_growth",
) -> EntryZones:
    """Type-aware price ladder for stock picker cards.

    The output is a reference ladder, not an automatic buy instruction.
    `comfortable_buy` and `deep_value` are always below current price; `avoid`
    is always above current price.
    """

    px = max(0.01, float(current_px))
    m50 = float(ma50 or px)
    m100 = float(ma100 or 0.0)
    m200 = float(ma200 or 0.0)
    l3 = max(0.01, float(low_3m or px * 0.92))
    l6 = max(0.01, float(low_6m or l3 * 0.95))
    h3 = max(px, float(high_3m or px))
    _h52 = max(h3, float(high_52w or h3))
    cfg = PULLBACK_CONFIG.get(stock_type, PULLBACK_CONFIG["steady_growth"])

    if m50 <= 0 or m200 <= 0:
        fair_anchor = px
    elif m100 and m100 > 0:
        fair_anchor = 0.45 * m50 + 0.35 * m100 + 0.20 * m200
    else:
        fair_anchor = 0.60 * m50 + 0.40 * m200

    # annualized_volatility can arrive as 0.24 or 24; normalize generously.
    vol = float(annualized_volatility or 0.25)
    if vol > 2:
        vol = vol / 100.0
    vol_adjust = clamp(vol / 0.25, 0.8, 1.6)
    discount = float(cfg["base_discount"]) * vol_adjust

    buy_low = fair_anchor * (1 - discount)
    buy_high = fair_anchor * (1 + discount * 0.5)
    buy_low = min(buy_low, l3 * 1.08)
    buy_high = min(buy_high, px * 1.03)

    comfortable = min(
        px * (1 - float(cfg["comfortable_pct"])),
        buy_low + max(0.0, px - buy_low) * 0.35,
        m50 if m50 and m50 < px else px * 0.97,
        px * 0.97,
    )
    comfortable = min(comfortable, px * 0.98)

    deep = min(
        px * (1 - float(cfg["deep_pct"])),
        px * 0.90,
        buy_low * 0.95,
        m200 if m200 else px * 0.90,
        l6 * 1.03,
    )
    deep = min(deep, comfortable * 0.95)

    avoid = max(
        buy_high * 1.05,
        px * 1.05,
    )
    avoid = max(avoid, px * 1.03)

    fixed = validate_price_ladder(
        current_price=px,
        recommended_low=buy_low,
        recommended_high=buy_high,
        comfortable_buy=comfortable,
        deep_value=deep,
        avoid_above=avoid,
    )

    if fixed["recommended_low"] <= px <= fixed["recommended_high"]:
        status = "当前可分批"
    elif px > fixed["recommended_high"]:
        status = "等回推荐区间"
    else:
        status = "低于推荐区间，检查趋势"

    trend_warning = bool(m200 and px < m200 * 0.95)

    return EntryZones(
        current_px=round(px, 2),
        deep_value=fixed["deep_value"],
        recommended_low=fixed["recommended_low"],
        recommended_high=fixed["recommended_high"],
        avoid_above=fixed["avoid_above"],
        comfortable_buy=fixed["comfortable_buy"],
        status=status,
        stock_type=stock_type,
        trend_warning=trend_warning,
    )


def calc_exit_zones(*, current_px: float, ma50: float, ma200: float, avoid_above: float) -> ExitZones:
    px = max(0.01, float(current_px))
    m50 = max(0.01, float(ma50 or px))
    m200 = max(0.01, float(ma200 or m50))
    avoid = max(0.01, float(avoid_above or px * 1.12))

    fair_ref = (m50 * 0.65) + (m200 * 0.35)
    trim_watch = max(px * 1.04, fair_ref * 1.12)
    pullback_risk = max(trim_watch * 1.03, avoid)
    trend_break = min(px * 0.92, max(m50 * 0.97, m200 * 1.02))

    return ExitZones(
        trim_watch=round(trim_watch, 2),
        pullback_risk=round(pullback_risk, 2),
        trend_break=round(trend_break, 2),
    )
