"""Contract tests for the typed API envelope (ADR-001).

Pure: no engine, no pandas, no FastAPI — runs anywhere pytest + pydantic exist, so it
stays green in the offline CI job while still proving the honesty-bearing contract.
"""
from __future__ import annotations

import api_envelope as env
from api_envelope import ApiEnvelope, today_meta, parse_sgt, current_mode


def test_envelope_shape_has_all_contract_fields():
    e = ApiEnvelope[dict](data={"x": 1}, state="ok", mode="live")
    dumped = e.model_dump()
    for key in ("data", "state", "mode", "observed_at", "fetched_at", "sources", "warnings"):
        assert key in dumped


def test_state_ok_when_no_holdings_degraded():
    advice = {"stocks": [{"ticker": "MSFT"}, {"ticker": "NVDA"}],
              "data_degraded": 0, "as_of": "2026-07-25 00:40", "source": "rule_based"}
    meta = today_meta(advice)
    assert meta["state"] == "ok"
    assert meta["sources"] == ["rule_based"]
    # observed_at is honestly null (not fabricated to now())
    assert meta["observed_at"] is None
    assert any("observation time" in w for w in meta["warnings"])


def test_state_partial_when_some_holdings_fell_back():
    advice = {"stocks": [{"ticker": "MSFT"}, {"ticker": "NVDA"}, {"ticker": "AAPL"}],
              "data_degraded": 1, "as_of": "2026-07-25 00:40", "source": "rule_based"}
    meta = today_meta(advice)
    assert meta["state"] == "partial"
    assert any("1 of 3" in w for w in meta["warnings"])


def test_state_unavailable_when_all_degraded_or_empty():
    all_bad = {"stocks": [{"ticker": "MSFT"}], "data_degraded": 1,
               "as_of": "2026-07-25 00:40", "source": "rule_based"}
    assert today_meta(all_bad)["state"] == "unavailable"
    empty = {"stocks": [], "data_degraded": 0, "as_of": "2026-07-25 00:40"}
    assert today_meta(empty)["state"] == "unavailable"


def test_fetched_at_is_real_engine_time_or_null():
    good = today_meta({"stocks": [{"t": 1}], "data_degraded": 0, "as_of": "2026-07-25 00:40"})
    assert good["fetched_at"] == "2026-07-25T00:40:00+08:00"
    bad = today_meta({"stocks": [{"t": 1}], "data_degraded": 0, "as_of": "not-a-time"})
    assert bad["fetched_at"] is None
    assert any("compute time" in w for w in bad["warnings"])


def test_parse_sgt_roundtrip_and_failure():
    assert parse_sgt("2026-07-25 09:05") == "2026-07-25T09:05:00+08:00"
    assert parse_sgt("") is None
    assert parse_sgt("garbage") is None


def test_current_mode_reads_env(monkeypatch):
    monkeypatch.delenv("ALPHAWATCH_MODE", raising=False)
    monkeypatch.delenv("ALPHAWATCH_OFFLINE", raising=False)
    assert current_mode() == "live"
    monkeypatch.setenv("ALPHAWATCH_OFFLINE", "1")
    assert current_mode() == "demo"
    monkeypatch.setenv("ALPHAWATCH_OFFLINE", "")
    monkeypatch.setenv("ALPHAWATCH_MODE", "demo")
    assert current_mode() == "demo"
