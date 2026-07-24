"""Tests for conviction history snapshots (pure + store)."""
from __future__ import annotations

from tasks import conviction_history as ch


def _focus(conv=70.0, factors=None):
    return {"conviction": conv, "direction": "偏多留意", "factors": factors or [{"label": "RSI", "detail": "28", "delta": 10}]}


def test_make_snapshot_shape():
    s = ch.make_snapshot(_focus(72), day="2026-07-24")
    assert s["day"] == "2026-07-24"
    assert s["conviction"] == 72.0
    assert s["factors"][0]["label"] == "RSI"


def test_latest_before():
    snaps = [
        {"day": "2026-07-20", "conviction": 60},
        {"day": "2026-07-22", "conviction": 65},
        {"day": "2026-07-24", "conviction": 70},
    ]
    assert ch.latest_before(snaps, "2026-07-24")["day"] == "2026-07-22"
    assert ch.latest_before(snaps, "2026-07-20") is None


def test_record_and_history(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_STORE", tmp_path / "ch.json")
    ch.record("MSFT", _focus(70), day="2026-07-23")
    ch.record("MSFT", _focus(78), day="2026-07-24")
    h = ch.history("MSFT")
    assert len(h) == 2
    assert h[-1]["conviction"] == 78.0


def test_record_replaces_same_day(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_STORE", tmp_path / "ch.json")
    ch.record("MSFT", _focus(70), day="2026-07-24")
    ch.record("MSFT", _focus(75), day="2026-07-24")   # same day -> replace
    h = ch.history("MSFT")
    assert len(h) == 1
    assert h[0]["conviction"] == 75.0
