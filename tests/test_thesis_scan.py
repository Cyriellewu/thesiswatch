"""Tests for thesis_scan orchestration + No-action brief (temp store, offline)."""
from __future__ import annotations

import pytest

from tasks import thesis_scan


@pytest.fixture
def temp_store(monkeypatch, tmp_path):
    from tasks import thesis_store as store
    monkeypatch.setattr(store, "_STORE", tmp_path / "ledger.json")
    return store


def _advice(**kw):
    base = {
        "stocks": [{"ticker": "MSFT", "action": "Hold", "reason_zh": "", "pos_52": 50, "rsi": 50, "chg_pct": 0.5}],
        "focus": [{"ticker": "MSFT", "conviction": 55, "direction": "中性观望", "factors": []}],
        "portfolio": {"concentration_flags": []},
        "news": {"holdings": []},
        "as_of_label": "now",
    }
    base.update(kw)
    return base


def test_scan_calm_portfolio_is_no_action(temp_store):
    # First scan seeds; second scan with same signals should be no_action.
    thesis_scan.scan_portfolio(_advice(), persist=True)
    ps, statuses = thesis_scan.scan_portfolio(_advice(), persist=True)
    assert ps.overall == "no_action"
    assert "无需行动" in ps.headline_zh
    txt = thesis_scan.brief_text(ps, statuses)
    assert "无需" in txt


def test_scan_surfaces_new_evidence_as_watch(temp_store):
    thesis_scan.scan_portfolio(_advice(), persist=True)
    # New conviction factor appears -> new evidence -> at least watch.
    hot = _advice(focus=[{"ticker": "MSFT", "conviction": 62, "direction": "偏多留意",
                          "factors": [{"label": "RSI 超卖", "detail": "RSI 28", "delta": 10}]}])
    ps, statuses = thesis_scan.scan_portfolio(hot, persist=True)
    assert ps.overall in ("watch", "re_evaluate")


def test_scan_holding_without_thesis_degrades(temp_store):
    ps, statuses = thesis_scan.scan_portfolio(_advice(), persist=False)
    assert len(statuses) == 1
    assert statuses[0].ticker == "MSFT"


def test_brief_text_lists_attention(temp_store):
    # Seed then trigger invalidation via a 200-day break in the reason text.
    thesis_scan.scan_portfolio(_advice(), persist=True)
    # give MSFT an invalidation condition first
    from tasks.thesis import InvestmentThesis, Evidence
    t = temp_store.load_thesis("MSFT")
    t.invalidation_conditions = ["跌破 200 日均线"]
    temp_store.save_thesis(t)
    broken = _advice(stocks=[{"ticker": "MSFT", "action": "Avoid for now",
                              "reason_zh": "已跌破 200 日均线", "pos_52": 8, "rsi": 25, "chg_pct": -7}])
    ps, statuses = thesis_scan.scan_portfolio(broken, persist=False)
    assert ps.overall == "re_evaluate"
    txt = thesis_scan.brief_text(ps, statuses)
    assert "MSFT" in txt
