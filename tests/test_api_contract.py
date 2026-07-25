from __future__ import annotations

from fastapi.testclient import TestClient

import api_server
from api_server import app

client = TestClient(app)


def _assert_envelope_shape(body: dict) -> None:
    for key in ["data", "state", "mode", "observed_at", "fetched_at", "sources", "warnings"]:
        assert key in body
    assert body["state"] in {"ok", "stale", "partial", "unavailable"}
    assert body["mode"] in {"demo", "live"}
    assert isinstance(body["sources"], list)
    assert isinstance(body["warnings"], list)


def test_today_envelope_contract() -> None:
    res = client.get("/api/today")
    assert res.status_code == 200
    body = res.json()
    _assert_envelope_shape(body)


def test_detail_endpoints_envelope_contract() -> None:
    for endpoint in ["/api/why/MSFT", "/api/evidence/MSFT", "/api/thesis/MSFT", "/api/exposures"]:
        res = client.get(endpoint)
        assert res.status_code == 200
        body = res.json()
        _assert_envelope_shape(body)



def test_detail_payload_shapes_in_demo_mode() -> None:
    why_body = client.get("/api/why/MSFT?mode=demo").json()
    _assert_envelope_shape(why_body)
    assert why_body["mode"] == "demo"
    assert isinstance(why_body["data"], dict)
    for key in ["ticker", "prevConviction", "currentConviction", "drivers", "meaningForYou", "status", "asOf", "dataState"]:
        assert key in why_body["data"]

    evidence_body = client.get("/api/evidence/MSFT?mode=demo").json()
    _assert_envelope_shape(evidence_body)
    assert evidence_body["mode"] == "demo"
    assert isinstance(evidence_body["data"], list)
    if evidence_body["data"]:
        item = evidence_body["data"][0]
        for key in ["id", "claim", "type", "sourceName", "observedAt", "fetchedAt", "freshness", "confidence", "thesisClaimId", "sign"]:
            assert key in item

    thesis_body = client.get("/api/thesis/MSFT?mode=demo").json()
    _assert_envelope_shape(thesis_body)
    assert thesis_body["mode"] == "demo"
    assert isinstance(thesis_body["data"], dict)
    for key in ["ticker", "status", "oneLiner", "conviction", "whatChanged", "currentThesis", "supporting", "risks", "asOf"]:
        assert key in thesis_body["data"]
    assert isinstance(thesis_body["data"]["whatChanged"], list)

    exposures_body = client.get("/api/exposures?mode=demo").json()
    _assert_envelope_shape(exposures_body)
    assert exposures_body["mode"] == "demo"
    assert isinstance(exposures_body["data"], list)
    if exposures_body["data"]:
        row = exposures_body["data"][0]
        for key in ["factor", "level", "holdings"]:
            assert key in row
        assert row["level"] in {"high", "medium", "low"}

def test_today_demo_mode() -> None:
    res = client.get("/api/today?mode=demo")
    assert res.status_code == 200
    assert res.json()["mode"] == "demo"


def test_today_observed_at_unknown_is_partial(monkeypatch) -> None:
    original = api_server.willow_agent.build_advice

    def _advice_with_unknown() -> dict:
        out = original()
        out["observed_at"] = None
        return out

    monkeypatch.setattr(api_server.willow_agent, "build_advice", _advice_with_unknown)
    res = client.get("/api/today")
    body = res.json()
    assert body["observed_at"] is None
    assert body["state"] in {"partial", "unavailable"}
    assert any("observation time unknown" in w for w in body["warnings"])


def test_why_failure_returns_unavailable(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(api_server, "_compute_why_live", _boom)
    res = client.get("/api/why/MSFT")
    body = res.json()
    assert body["state"] == "unavailable"
    assert body["data"] is None
    assert body["warnings"]


def test_health_still_ok() -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
