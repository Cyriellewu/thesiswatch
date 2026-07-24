"""Broad market regime from SPY / QQQ vs moving averages (Elder-style environment)."""

from __future__ import annotations

from dataclasses import dataclass

from signals.indicators import bars_summary, load_daily_bars


@dataclass(frozen=True)
class MarketRegime:
    label: str  # bullish | caution | risk_off
    zh: str
    detail: str


def compute_market_regime() -> MarketRegime:
    spy = load_daily_bars("SPY")
    qqq = load_daily_bars("QQQ")
    s = bars_summary(spy) if spy is not None else {}
    q = bars_summary(qqq) if qqq is not None else {}

    def _ok(d: dict, key: str) -> float:
        v = d.get(key)
        return float(v) if v is not None else float("nan")

    spy_c, spy50, spy200 = _ok(s, "close"), _ok(s, "ma50"), _ok(s, "ma200")
    qqq_c, qqq50, qqq200 = _ok(q, "close"), _ok(q, "ma50"), _ok(q, "ma200")

    if any(x != x for x in (spy_c, spy50, spy200, qqq_c, qqq50, qqq200)):
        return MarketRegime("caution", "谨慎", "大盘数据暂不全，默认不放大中短期追涨信号。")

    spy_weak = spy_c < spy200 or qqq_c < qqq200
    spy_caution = spy_c < spy50 or qqq_c < qqq50

    if spy_weak:
        return MarketRegime(
            "risk_off",
            "风险关闭",
            "SPY 或 QQQ 收于 MA200 下方：环境偏弱，个股买点要求更严，原则上不给「突破追涨」类触发。",
        )
    if spy_caution:
        return MarketRegime(
            "caution",
            "谨慎",
            "SPY 或 QQQ 收于 MA50 下方：趋势未共振，允许观察/等 setup，不轻易给「买点触发」。",
        )
    return MarketRegime(
        "bullish",
        "偏强",
        "SPY 与 QQQ 均在 MA50 / MA200 之上：环境对顺势突破更友好（仍需个股结构 + 风险收益）。",
    )
