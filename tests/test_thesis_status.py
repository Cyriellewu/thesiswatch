"""Tests for the thesis status engine (No action / Watch / Re-evaluate)."""
from __future__ import annotations

from datetime import datetime, timezone

from tasks.thesis import Evidence, InvestmentThesis
from tasks.thesis_delta import diff_thesis
from tasks.thesis_status import classify, summarize_portfolio

_NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _thesis(**kw):
    base = dict(
        ticker="MSFT", claims=["Azure 强"], catalysts=["财报"], risks=["资本开支"],
        invalidation_conditions=["Azure 增长低于 20%"],
        evidence=[Evidence("Azure 29%", "fundamental", "support", weight=2.0, observed_at=_NOW.isoformat())],
    )
    base.update(kw)
    return InvestmentThesis(**base)


def test_no_change_is_no_action():
    t = _thesis()
    d = diff_thesis(t, t, now=_NOW)
    s = classify(t, d, now=_NOW)
    assert s.status == "no_action"
    assert "未变" in s.headline_zh or "无" in s.headline_zh


def test_new_evidence_is_watch():
    before = _thesis(evidence=[
        Evidence("Azure 29%", "fundamental", "support", weight=2.0, observed_at=_NOW.isoformat()),
        Evidence("云担忧", "fundamental", "counter", weight=2.0, observed_at=_NOW.isoformat()),
    ])
    after = _thesis(evidence=before.evidence + [
        Evidence("新政策风险", "macro", "counter", weight=1.0, observed_at=_NOW.isoformat()),
    ])
    d = diff_thesis(before, after, now=_NOW)
    s = classify(after, d, now=_NOW)
    assert s.status in ("watch", "re_evaluate")  # a small change -> at least watch


def test_invalidation_is_re_evaluate():
    t = _thesis()
    d = diff_thesis(t, t, signals={"azure": True}, now=_NOW)
    s = classify(t, d, now=_NOW)
    assert s.status == "re_evaluate"
    assert "失效" in " ".join(s.reasons)


def test_big_confidence_swing_is_re_evaluate():
    before = _thesis(evidence=[
        Evidence("好1", "fundamental", "support", weight=3.0, observed_at=_NOW.isoformat()),
        Evidence("坏1", "fundamental", "counter", weight=0.5, observed_at=_NOW.isoformat()),
    ])
    after = _thesis(evidence=[
        Evidence("坏1", "fundamental", "counter", weight=3.0, observed_at=_NOW.isoformat()),
        Evidence("坏2", "fundamental", "counter", weight=3.0, observed_at=_NOW.isoformat()),
    ])
    d = diff_thesis(before, after, now=_NOW)
    s = classify(after, d, now=_NOW)
    assert s.status == "re_evaluate"


def test_portfolio_all_calm_says_no_action():
    t = _thesis()
    d = diff_thesis(t, t, now=_NOW)
    statuses = [classify(t, d, now=_NOW)]
    p = summarize_portfolio(statuses)
    assert p.overall == "no_action"
    assert "无需行动" in p.headline_zh
    assert p.no_change and not p.needs_attention


def test_portfolio_surfaces_attention():
    t = _thesis()
    calm = classify(t, diff_thesis(t, t, now=_NOW), now=_NOW)
    hot = classify(t, diff_thesis(t, t, signals={"azure": True}, now=_NOW), now=_NOW)
    p = summarize_portfolio([calm, hot])
    assert p.overall == "re_evaluate"
    assert len(p.needs_attention) == 1
