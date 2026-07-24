"""Tests for the Thesis Ledger + Thesis Delta (pure, offline)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tasks.thesis import Evidence, InvestmentThesis
from tasks.thesis_delta import diff_thesis

_NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _thesis(**kw) -> InvestmentThesis:
    base = dict(
        ticker="MSFT",
        claims=["Azure 增长高于同业"],
        catalysts=["Copilot 收入披露"],
        risks=["AI 资本开支压力"],
        invalidation_conditions=["Azure 增长连续两季低于 20%"],
        evidence=[Evidence("Azure 增速 29%", "fundamental", "support", weight=2.0, observed_at=_iso(3))],
    )
    base.update(kw)
    return InvestmentThesis(**base)


# ---- thesis model ---------------------------------------------------------


def test_coverage_full_vs_partial():
    full = _thesis()
    assert full.coverage() == 1.0
    thin = InvestmentThesis(ticker="X", claims=["只有一个想法"])
    assert thin.coverage() < 0.5


def test_freshness_uses_most_recent_evidence():
    t = _thesis(evidence=[
        Evidence("旧", "news", "support", observed_at=_iso(40)),
        Evidence("新", "price", "support", observed_at=_iso(2)),
    ])
    assert t.freshness_days(now=_NOW) == 2.0


def test_confidence_is_structured_not_bare_number():
    c = _thesis().confidence(now=_NOW)
    assert set(c) >= {"score", "level", "coverage", "freshness_days"}
    assert 0 <= c["score"] <= 100
    assert c["level"] in {"unknown", "low", "medium", "high"}


def test_counter_evidence_lowers_confidence():
    support_only = _thesis()
    with_counter = _thesis(evidence=[
        Evidence("Azure 增速 29%", "fundamental", "support", weight=2.0, observed_at=_iso(3)),
        Evidence("云增长减速", "fundamental", "counter", weight=2.0, observed_at=_iso(1)),
    ])
    assert with_counter.confidence(now=_NOW)["score"] < support_only.confidence(now=_NOW)["score"]


def test_incomplete_thesis_confidence_unknown():
    thin = InvestmentThesis(ticker="X", claims=["一个想法"])
    assert thin.confidence(now=_NOW)["level"] == "unknown"


def test_stale_evidence_dampens_confidence():
    fresh = _thesis(evidence=[Evidence("好消息", "news", "support", weight=3.0, observed_at=_iso(1))])
    stale = _thesis(evidence=[Evidence("好消息", "news", "support", weight=3.0, observed_at=_iso(90))])
    assert stale.confidence(now=_NOW)["score"] < fresh.confidence(now=_NOW)["score"]


def test_invalidation_hits_only_on_keyword_match():
    t = _thesis(invalidation_conditions=["Azure 增长连续两季低于 20%"])
    assert t.invalidation_hits({"azure": True}) == ["Azure 增长连续两季低于 20%"]
    assert t.invalidation_hits({"azure": False}) == []
    assert t.invalidation_hits({"unrelated": True}) == []


def test_roundtrip_serialization():
    t = _thesis()
    t2 = InvestmentThesis.from_dict(t.to_dict())
    assert t2.ticker == "MSFT"
    assert t2.evidence[0].text == "Azure 增速 29%"


# ---- thesis delta ---------------------------------------------------------


def test_first_time_thesis():
    d = diff_thesis(None, _thesis(), now=_NOW)
    assert "首次建立" in d.summary_zh
    assert d.direction == "no_material_change"


def test_new_supporting_evidence_strengthens():
    # Start from a balanced thesis so new support actually shifts the balance.
    before = _thesis(evidence=[
        Evidence("Azure 增速 29%", "fundamental", "support", weight=2.0, observed_at=_iso(3)),
        Evidence("云增长减速担忧", "fundamental", "counter", weight=2.0, observed_at=_iso(2)),
    ])
    after = _thesis(evidence=before.evidence + [
        Evidence("Copilot 收入超预期", "fundamental", "support", weight=3.0, observed_at=_iso(0)),
    ])
    d = diff_thesis(before, after, now=_NOW)
    assert d.direction == "strengthened"
    assert d.confidence_after > d.confidence_before
    assert len(d.new_evidence) == 1


def test_invalidation_trigger_forces_weakened():
    before = _thesis()
    after = _thesis()
    d = diff_thesis(before, after, signals={"azure": True}, now=_NOW)
    assert d.direction == "weakened"
    assert d.invalidation_triggered
    assert "⚠️" in d.summary_zh


def test_removed_risk_tracked():
    before = _thesis(risks=["AI 资本开支压力", "监管风险"])
    after = _thesis(risks=["AI 资本开支压力"])
    d = diff_thesis(before, after, now=_NOW)
    assert "监管风险" in d.removed_risks


def test_no_material_change():
    d = diff_thesis(_thesis(), _thesis(), now=_NOW)
    assert d.direction == "no_material_change"
    assert "无实质变化" in d.summary_zh


# ---- thesis store ---------------------------------------------------------


def test_store_save_load_and_history(monkeypatch, tmp_path):
    from tasks import thesis_store as store

    monkeypatch.setattr(store, "_STORE", tmp_path / "ledger.json")

    assert store.load_thesis("MSFT") is None
    t1 = _thesis(claims=["版本1"])
    store.save_thesis(t1)
    assert store.load_thesis("MSFT").claims == ["版本1"]
    assert store.load_previous("MSFT") is None      # no prior yet
    assert store.all_tickers() == ["MSFT"]

    t2 = _thesis(claims=["版本2"])
    store.save_thesis(t2)
    assert store.load_thesis("MSFT").claims == ["版本2"]
    assert store.load_previous("MSFT").claims == ["版本1"]   # prior snapshot kept


def test_store_delete(monkeypatch, tmp_path):
    from tasks import thesis_store as store

    monkeypatch.setattr(store, "_STORE", tmp_path / "ledger.json")
    store.save_thesis(_thesis())
    store.delete_thesis("MSFT")
    assert store.load_thesis("MSFT") is None
    assert store.all_tickers() == []
