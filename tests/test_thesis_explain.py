"""Tests for thesis_explain: factor_delta + classify_evidence (offline)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tasks.thesis import Evidence, InvestmentThesis
from tasks.thesis_explain import classify_evidence, factor_delta

_NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _iso(days):
    return (_NOW - timedelta(days=days)).isoformat()


def test_factor_delta_new_and_changed():
    before = [{"label": "技术信号", "detail": "持有", "delta": 5}]
    after = [
        {"label": "技术信号", "detail": "逢低加", "delta": 9},   # +4
        {"label": "大佬持仓", "detail": "3位", "delta": 6},       # new +6
    ]
    d = factor_delta(before, after)
    labels = {m["label"]: m for m in d["movers"]}
    assert labels["大佬持仓"]["kind"] == "new"
    assert labels["技术信号"]["move"] == 4.0
    # biggest mover first
    assert d["movers"][0]["label"] == "大佬持仓"


def test_factor_delta_removed():
    d = factor_delta([{"label": "新闻", "detail": "x", "delta": 4}], [])
    assert d["movers"][0]["kind"] == "removed"
    assert d["movers"][0]["move"] == -4.0


def test_factor_delta_drops_unchanged():
    same = [{"label": "RSI", "detail": "50", "delta": 0}]
    d = factor_delta(same, same)
    assert d["movers"] == []


def test_classify_evidence_buckets():
    t = InvestmentThesis(
        ticker="MSFT",
        evidence=[
            Evidence("Azure 29%", "fundamental", "support", observed_at=_iso(1)),   # fact
            Evidence("大佬持有", "smart_money", "support", observed_at=_iso(2)),      # interpretation
            Evidence("我认为云需求持续", "user", "support", observed_at=_iso(3)),       # assumption
            Evidence("资本开支担忧", "fundamental", "counter", observed_at=_iso(1)),   # counter
        ],
    )
    c = classify_evidence(t, now=_NOW)
    assert len(c["facts"]) == 1
    assert len(c["interpretations"]) == 1
    assert len(c["assumptions"]) == 1
    assert len(c["counterarguments"]) == 1
    assert c["conflict"] is True   # has both support and counter


def test_no_conflict_when_all_support():
    t = InvestmentThesis(ticker="X", evidence=[
        Evidence("好", "fundamental", "support", observed_at=_iso(1)),
    ])
    c = classify_evidence(t, now=_NOW)
    assert c["conflict"] is False


def test_freshness_labels():
    t = InvestmentThesis(ticker="X", evidence=[
        Evidence("旧", "news", "support", observed_at=_iso(90)),
    ])
    c = classify_evidence(t, now=_NOW)
    assert "过期" in c["interpretations"][0]["freshness"]
