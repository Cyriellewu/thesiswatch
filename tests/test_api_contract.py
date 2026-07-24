from __future__ import annotations

from fastapi.testclient import TestClient

import api_server
from api_server import app

client = TestClient(app)


def test_today_envelope_contract() -> None:
    res = client.get("/api/today")
    assert res.status_code == 200
    body = res.json()
    for key in ["data", "state", "mode", "observed_at", "fetched_at", "sources", "warnings"]:
        assert key in body
    assert body["state"] in {"ok", "stale", "partial", "unavailable"}
    assert body["mode"] in {"demo", "live"}
    assert isinstance(body["sources"], list)
    assert isinstance(body["warnings"], list)


def test_today_demo_mode() -> None:
    res = client.get("/api/today?mode=demo")
    assert res.status_code == 200
    assert res.json()["mode"] == "demo"


def test_today_engine_failure_returns_unavailable(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(api_server.willow_agent, "build_advice", _boom)
    res = client.get("/api/today")
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "unavailable"
    assert body["data"] is None
    assert body["warnings"]


def test_health_still_ok() -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
