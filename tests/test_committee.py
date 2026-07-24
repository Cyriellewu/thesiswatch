"""Tests for the investment committee (pure core + factguard). Offline, no LLM."""
from __future__ import annotations

from tasks import committee as c


def _stock(**kw):
    base = {
        "ticker": "NVDA", "action": "Hold", "action_zh": "持有",
        "rsi": 50, "pos_52": 50, "chg_pct": 0.0, "weight_pct": 10.0, "pnl_pct": 0.0,
    }
    base.update(kw)
    return base


def _focus(conviction=50.0, direction="中性观望", factors=None):
    return {"conviction": conviction, "direction": direction, "factors": factors or []}


def test_build_facts_returns_four_personas():
    views = c.build_committee_facts(_stock(), _focus(), [], None, False)
    assert [v.persona for v in views] == ["value", "momentum", "risk", "smartmoney"]


def test_risk_manager_vetoes_on_single_name_flag():
    views = c.build_committee_facts(
        _stock(ticker="NVDA", weight_pct=30), _focus(),
        ["单票偏重(NVDA 约 30%)"], None, False, scope="stock",
        portfolio={"tech_weight_pct": 60},
    )
    risk = next(v for v in views if v.persona == "risk")
    assert risk.veto is True
    verdict = c.synthesize_rule_based("NVDA", "stock", views, "now")
    assert c._VETO_WARNING in verdict.warnings
    assert verdict.confidence <= 35
    assert verdict.stance == "veto"


def test_smartmoney_neutral_not_bearish_when_no_gurus():
    views = c.build_committee_facts(_stock(), _focus(), [], [], False)  # empty overlap
    sm = next(v for v in views if v.persona == "smartmoney")
    assert sm.stance == "neutral"
    assert sm.stance != "bearish"
    assert any("13F" in b or "看不到" in b for b in sm.bullets)


def test_smartmoney_none_when_not_loaded():
    views = c.build_committee_facts(_stock(), _focus(), [], None, False)
    sm = next(v for v in views if v.persona == "smartmoney")
    assert sm.stance == "neutral"
    assert any("未加载" in b for b in sm.bullets)


def test_momentum_reuses_conviction_factors():
    fe = _focus(conviction=72, direction="重点关注",
                factors=[{"label": "RSI 超卖", "detail": "RSI 28", "delta": 10}])
    views = c.build_committee_facts(_stock(), fe, [], None, False)
    mom = next(v for v in views if v.persona == "momentum")
    assert mom.score == 72
    assert mom.stance == "bullish"
    assert any("RSI 超卖" in b for b in mom.bullets)


def test_value_tags_valuation_proxy():
    views = c.build_committee_facts(_stock(), _focus(), [], None, False)
    val = next(v for v in views if v.persona == "value")
    assert any("代理" in b for b in val.bullets)


def test_chair_cites_personas():
    views = c.build_committee_facts(_stock(), _focus(), [], None, False)
    v = c.synthesize_rule_based("NVDA", "stock", views, "now")
    named = sum(name in v.rationale_zh for name in ("价值派", "动量派", "风控官", "大佬跟随"))
    assert named >= 2


def test_split_committee_low_confidence():
    fe = _focus(conviction=90, direction="重点关注")
    views = c.build_committee_facts(
        _stock(pos_52=95), fe, [], [], False,  # value bearish (high), momentum bullish (90)
    )
    v = c.synthesize_rule_based("NVDA", "stock", views, "now")
    high = c.synthesize_rule_based(
        "NVDA", "stock",
        c.build_committee_facts(_stock(), _focus(50), [], [], False), "now",
    )
    assert v.confidence < high.confidence  # more spread -> less confidence


def test_build_committee_facts_never_raises_on_missing_fields():
    views = c.build_committee_facts(
        {"ticker": "X"}, None, [], None, False  # almost all fields missing
    )
    assert len(views) == 4
    # must not raise; a verdict must still form
    v = c.synthesize_rule_based("X", "stock", views, "now")
    assert isinstance(v.confidence, int)


def test_offline_verdict_is_deterministic():
    a = c.run_committee_for_stock(
        {"stocks": [_stock()], "focus": [dict(_focus(), ticker="NVDA")],
         "portfolio": {"concentration_flags": []}, "news": {"holdings": []},
         "as_of_label": "now"},
        "NVDA", use_llm=False,
    )
    b = c.run_committee_for_stock(
        {"stocks": [_stock()], "focus": [dict(_focus(), ticker="NVDA")],
         "portfolio": {"concentration_flags": []}, "news": {"holdings": []},
         "as_of_label": "now"},
        "NVDA", use_llm=False,
    )
    assert a == b


class _StubLLM(c.CommitteeLLM):
    def __init__(self, inject: str):
        super().__init__(guard=object(), chair_guard=object())
        self._inject = inject

    def _call(self, guard, task, prompt):
        return self._inject


def test_llm_factguard_rejects_new_numbers():
    views = c.build_committee_facts(_stock(), _focus(), [], None, False)
    val = next(v for v in views if v.persona == "value")
    llm = _StubLLM("这只股票 P/E 只有 12，非常便宜。")  # 12 is a fabricated number
    out = llm.polish_persona(val)
    assert out.source == "rule"  # prose discarded
    assert "12" not in out.prose_zh


def test_llm_factguard_accepts_faithful_rephrase():
    v = c.PersonaView("value", "价值派", "neutral", 50.0, ["接近年内低位（52周位置 20%）。"])
    llm = _StubLLM("目前处于年内低位（52周位置 20% 附近），估值相对占优。")
    out = llm.polish_persona(v)
    assert out.source == "llm"


def test_portfolio_committee_runs():
    advice = {
        "stocks": [_stock()], "focus": [dict(_focus(60), ticker="NVDA")],
        "portfolio": {"concentration_flags": ["科技偏重"], "tech_weight_pct": 70, "risk_level": "attention"},
        "news": {"holdings": []}, "as_of_label": "now",
    }
    v = c.run_committee_for_portfolio(advice, use_llm=False)
    assert v.scope == "portfolio"
    assert v.warnings or v.rationale_zh
