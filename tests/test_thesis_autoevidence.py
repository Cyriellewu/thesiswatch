"""Tests for auto-evidence + the thesis status engine (No action/Watch/Re-evaluate)."""
from __future__ import annotations

from tasks.thesis_autoevidence import evidence_from_signals, signals_for_invalidation


def _stock(**kw):
    base = {"ticker": "MSFT", "action": "Hold", "reason_zh": "", "pos_52": 50, "rsi": 50, "chg_pct": 0.0}
    base.update(kw)
    return base


def _focus(factors=None):
    return {"conviction": 60, "direction": "偏多留意", "factors": factors or []}


def test_conviction_factors_become_evidence():
    fe = _focus([
        {"label": "RSI 超卖", "detail": "RSI 28", "delta": 10},
        {"label": "高波动", "detail": "20日年化 70%", "delta": -8},
    ])
    ev = evidence_from_signals(_stock(), fe, None, False)
    assert len(ev) == 2
    stances = {e.text.split("：")[0]: e.stance for e in ev}
    assert stances["RSI 超卖"] == "support"
    assert stances["高波动"] == "counter"


def test_guru_overlap_becomes_smart_money_evidence():
    ev = evidence_from_signals(_stock(), _focus(), [{"label": "Buffett (BRK)", "pct": 8.0}], False)
    g = [e for e in ev if e.kind == "smart_money"]
    assert g and g[0].stance == "support" and "Buffett" in g[0].text


def test_concentration_becomes_counter_evidence():
    ev = evidence_from_signals(_stock(ticker="NVDA"), _focus(), None, False,
                               concentration_flags=["单票偏重(NVDA 约 30%)"])
    c = [e for e in ev if e.source == "auto:concentration"]
    assert c and c[0].stance == "counter"


def test_signals_for_invalidation_rsi_and_200d():
    sig = signals_for_invalidation(_stock(reason_zh="已跌破 200 日均线", rsi=25))
    assert sig.get("200日") is True
    assert sig.get("rsi") is True


def test_signals_conservative_when_calm():
    sig = signals_for_invalidation(_stock())
    assert not any(sig.values())
