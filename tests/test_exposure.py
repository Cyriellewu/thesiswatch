"""Tests for portfolio exposure + what-if simulator (offline)."""
from __future__ import annotations

from tasks import exposure


def test_semis_exposure_aggregates():
    pos = [
        {"ticker": "NVDA", "weight_pct": 30.0},
        {"ticker": "AVGO", "weight_pct": 20.0},
        {"ticker": "MSFT", "weight_pct": 50.0},
    ]
    exps = exposure.compute_exposures(pos)
    themes = {e.theme: e for e in exps}
    # Semis = NVDA(30*1.0) + AVGO(20*1.0) = 50
    assert themes["半导体"].pct == 50.0
    assert themes["半导体"].level == "高"
    # contributors sorted, NVDA first
    assert themes["半导体"].contributors[0]["ticker"] == "NVDA"


def test_hidden_ai_exposure_across_diversified_looking_holdings():
    # Looks diversified (5 names) but AI infra is a common bet.
    pos = [
        {"ticker": "NVDA", "weight_pct": 20},
        {"ticker": "MSFT", "weight_pct": 20},
        {"ticker": "GOOGL", "weight_pct": 20},
        {"ticker": "QQQ", "weight_pct": 20},
        {"ticker": "AVGO", "weight_pct": 20},
    ]
    exps = {e.theme: e.pct for e in exposure.compute_exposures(pos)}
    assert exps["AI 基础设施"] > 40  # meaningful hidden common factor


def test_unknown_ticker_contributes_nothing():
    exps = exposure.compute_exposures([{"ticker": "ZZZZ", "weight_pct": 100}])
    assert exps == []


def test_simulate_add_shifts_exposure_and_cash():
    pos = [
        {"ticker": "NVDA", "weight_pct": 50, "mv": 5000},
        {"ticker": "QQQ", "weight_pct": 50, "mv": 5000},
    ]
    res = exposure.simulate(pos, cash=2000, changes=[{"ticker": "QQQ", "delta_usd": 1000}])
    # cash dropped by 1000
    assert res["after_cash_pct"] < (2000 / 12000 * 100)
    # NVDA's own weight fell after adding to QQQ
    after = {p["ticker"]: p["weight_pct"] for p in res["after_positions"]}
    assert after["NVDA"] < 50


def test_simulate_reduce():
    pos = [{"ticker": "NVDA", "weight_pct": 60, "mv": 6000}, {"ticker": "VOO", "weight_pct": 40, "mv": 4000}]
    res = exposure.simulate(pos, cash=0, changes=[{"ticker": "NVDA", "delta_usd": -2000}])
    after = {p["ticker"]: p["weight_pct"] for p in res["after_positions"]}
    assert after["NVDA"] < 60  # reduced


def test_simulate_does_not_mutate_input():
    pos = [{"ticker": "NVDA", "weight_pct": 100, "mv": 1000}]
    exposure.simulate(pos, cash=500, changes=[{"ticker": "NVDA", "delta_usd": -500}])
    assert pos[0]["mv"] == 1000  # unchanged
