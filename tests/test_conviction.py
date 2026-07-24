"""Tests for the multi-factor conviction scorer (offline, no network)."""
from __future__ import annotations

from tasks import conviction


def _stock(**kw):
    base = {
        "ticker": "NVDA",
        "action": "Hold",
        "action_zh": "持有",
        "rsi": 50,
        "pos_52": 50,
        "chg_pct": 0.0,
        "weight_pct": 10.0,
        "pnl_pct": 0.0,
    }
    base.update(kw)
    return base


def test_neutral_stock_scores_near_50():
    r = conviction.score_stock(_stock())
    assert 44 <= r["conviction"] <= 56
    assert r["direction"] == "中性观望"


def test_oversold_plus_add_on_weakness_raises_conviction():
    r = conviction.score_stock(_stock(action="Add on weakness", action_zh="逢低加", rsi=28, pos_52=20))
    assert r["conviction"] > 60
    labels = [f["label"] for f in r["factors"]]
    assert "RSI 超卖" in labels
    assert "技术信号" in labels
    assert "接近年内低位" in labels


def test_overbought_avoid_lowers_conviction():
    r = conviction.score_stock(_stock(action="Avoid for now", action_zh="先别碰", rsi=78, pos_52=95))
    assert r["conviction"] < 44
    assert r["direction"] == "谨慎回避"


def test_guru_holding_boosts_score():
    without = conviction.score_stock(_stock())
    with_guru = conviction.score_stock(_stock(), guru_pct=12.0, guru_count=3)
    assert with_guru["conviction"] > without["conviction"]
    assert any(f["label"] == "大佬持仓" for f in with_guru["factors"])


def test_momentum_capped():
    # A huge single-day move must not blow past the cap.
    r = conviction.score_stock(_stock(chg_pct=40.0))
    mom = next(f for f in r["factors"] if f["label"] == "今日动量")
    assert mom["delta"] <= 9


def test_score_bounded_0_100():
    hi = conviction.score_stock(
        _stock(action="Add on weakness", rsi=20, pos_52=10, chg_pct=30), guru_pct=50, guru_count=6, in_news=True
    )
    lo = conviction.score_stock(_stock(action="Avoid for now", rsi=85, pos_52=99, chg_pct=-30))
    assert 0 <= lo["conviction"] <= 100
    assert 0 <= hi["conviction"] <= 100


def test_build_focus_ranks_and_limits():
    stocks = [
        _stock(ticker="AAA", action="Avoid for now", rsi=80, pos_52=97),
        _stock(ticker="BBB", action="Add on weakness", rsi=27, pos_52=18),
        _stock(ticker="CCC", action="Hold"),
    ]
    gurus = [{"holdings": [{"symbol": "BBB", "pct": 8.0}]}]
    news = {"holdings": [{"ticker": "BBB"}]}
    focus = conviction.build_focus(stocks, gurus=gurus, news=news, top_n=2)
    assert len(focus) == 2
    assert focus[0]["ticker"] == "BBB"  # highest conviction first
    assert focus[0]["thesis"]


def test_build_focus_offline_without_context():
    stocks = [_stock(ticker="X"), _stock(ticker="Y", action="Add on weakness", rsi=30)]
    focus = conviction.build_focus(stocks)
    assert len(focus) == 2
    assert focus[0]["ticker"] == "Y"


def test_volatility_penalty_lowers_score():
    calm = conviction.score_stock(_stock())
    wild = conviction.score_stock(_stock(), vol_20d=70.0)
    assert wild["conviction"] < calm["conviction"]
    assert any(f["label"] == "高波动" for f in wild["factors"])


def test_volatility_penalty_wired_through_build_focus():
    stocks = [_stock(ticker="Z", vol_20d=65.0)]
    focus = conviction.build_focus(stocks)
    assert any(f["label"] == "高波动" for f in focus[0]["factors"])
