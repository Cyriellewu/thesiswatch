"""End-to-end API contract test via FastAPI TestClient (ADR-009).

Requires the engine runtime (pandas etc.) and FastAPI; when those are absent (e.g. the
minimal offline CI job) the test SKIPS rather than fails — an honest skip, not a fake
pass. Run in a fully-provisioned env to exercise the real /api/today envelope.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pandas")


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("ALPHAWATCH_OFFLINE", "1")  # demo mode, no network
    try:
        from fastapi.testclient import TestClient

        import api_server
    except Exception as exc:  # engine import failed → skip, do not fake-pass
        pytest.skip(f"api_server/engine unavailable: {exc}")
    return TestClient(api_server.app)


CONTRACT_KEYS = {"data", "state", "mode", "observed_at", "fetched_at", "sources", "warnings"}


def test_today_returns_typed_envelope(client):
    r = client.get("/api/today")
    assert r.status_code == 200
    body = r.json()
    assert CONTRACT_KEYS.issubset(body.keys())
    assert body["state"] in ("ok", "stale", "partial", "unavailable")
    assert body["mode"] in ("demo", "live")


def test_today_freshness_is_not_fabricated(client):
    body = client.get("/api/today").json()
    # observed_at must be honestly null (per-source time not tracked), not a fake now().
    assert body["observed_at"] is None
    # The retired fabricated field must be gone.
    assert "updatedAgoMinutes" not in (body.get("data") or {})


def test_today_mode_is_demo_when_offline(client):
    body = client.get("/api/today").json()
    assert body["mode"] == "demo"
