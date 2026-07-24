from __future__ import annotations

from types import SimpleNamespace

from stock_picker.practical_zones import build_price_ladder
from stock_picker.quality_filter import _advice_from_action, _current_action, _queue_from_action


def test_price_ladder_direction_examples():
    examples = [
        ("JNJ", 221.32, 210.25, 232.39, 206.32),
        ("PG", 146.42, 139.10, 153.74, 148.34),
        ("MCD", 275.75, 261.96, 289.54, 306.75),
        ("JPM", 302.10, 287.00, 317.00, 302.76),
        ("MA", 495.48, 470.00, 520.00, 545.26),
    ]
    for _sym, px, low, high, ma200 in examples:
        ladder = build_price_ladder(
            current_price=px,
            recommended_low=low,
            recommended_high=high,
            ma200=ma200,
        )
        assert ladder["deep_buy"] < ladder["pullback_buy"] < px < ladder["avoid_above"]


def test_observe_never_maps_to_now_buyable_queue():
    assert _queue_from_action("observe") == "小仓观察"
    assert _queue_from_action("cautious_buyable") == "小仓观察"
    assert _queue_from_action("priority_buyable") == "现在可分批"
    assert _queue_from_action("buyable") == "现在可分批"


def test_display_advice_is_derived_from_final_action():
    assert _advice_from_action("wait_pullback", "可分批") == "等回踩"
    assert _advice_from_action("dca_only", "可分批") == "只适合定投"
    assert _advice_from_action("momentum_alert_only", "可分批") == "重大异动，不追"


def test_weak_trend_cannot_be_buyable():
    action = _current_action(
        is_buyable_now=True,
        buyable_tier="priority_buyable",
        buy_mode="extra_add",
        advice="可分批",
        p_status="合理区间",
        q_score=9,
        t_score=3,
        chg_pct=0.0,
        bucket="消费防御",
        gap_score=2.0,
        stock_type="defensive_quality",
    )
    assert action == "cautious_buyable"


def test_tech_etf_overweight_is_dca_only():
    action = _current_action(
        is_buyable_now=False,
        buyable_tier="dca_only",
        buy_mode="dca_only",
        advice="只适合定投",
        p_status="合理区间",
        q_score=10,
        t_score=6,
        chg_pct=2.34,
        bucket="科技 / AI / 半导体",
        gap_score=-2.0,
        stock_type="etf_core",
    )
    assert action == "dca_only"
