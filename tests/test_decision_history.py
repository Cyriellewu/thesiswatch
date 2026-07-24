"""Tests for decision history (pure logic + store)."""
from __future__ import annotations

import pytest

from tasks import decision_history as dh


# ---- pure logic ----


def test_should_log_first_time():
    assert dh.should_log(None, "watch", 60) is True


def test_should_log_on_status_change():
    prev = {"status": "no_action", "confidence": 60}
    assert dh.should_log(prev, "watch", 60) is True


def test_should_log_on_big_confidence_move():
    prev = {"status": "watch", "confidence": 60}
    assert dh.should_log(prev, "watch", 65) is True
    assert dh.should_log(prev, "watch", 62) is False   # < 4 points


def test_score_outcome_computes_pct():
    e = dh.make_entry("MSFT", "re_evaluate", 70, ["论点增强"], price=100.0)
    scored = dh.score_outcome(e, 110.0)
    assert scored["outcome_pct"] == 10.0
    assert e["outcome_pct"] is None   # non-mutating


def test_accuracy_summary_agreement():
    entries = [
        dh.score_outcome(dh.make_entry("A", "re_evaluate", 70, ["论点增强"], price=100), 110),  # bullish, +10 -> agree
        dh.score_outcome(dh.make_entry("B", "re_evaluate", 40, ["失效条件"], price=100), 90),   # bearish, -10 -> agree
        dh.score_outcome(dh.make_entry("C", "re_evaluate", 60, ["论点增强"], price=100), 90),   # bullish, -10 -> disagree
    ]
    s = dh.accuracy_summary(entries)
    assert s["scored"] == 3
    assert s["agreement_pct"] == round(2 / 3 * 100, 0)


def test_accuracy_summary_empty():
    assert dh.accuracy_summary([])["scored"] == 0


# ---- store ----


def test_record_if_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(dh, "_STORE", tmp_path / "dh.json")
    assert dh.record_if_changed("MSFT", "no_action", 60, ["噪音"], price=100) is True
    assert dh.record_if_changed("MSFT", "no_action", 61, ["噪音"], price=101) is False  # tiny move
    assert dh.record_if_changed("MSFT", "watch", 61, ["新证据"], price=101) is True     # status change
    h = dh.history("MSFT")
    assert len(h) == 2
    assert h[-1]["status"] == "watch"
