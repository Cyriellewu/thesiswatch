from __future__ import annotations


def test_daily_refresh_shapes() -> None:
    from jobs import scheduler as sched

    out = sched.daily_refresh(["AAPL", "MSFT"])
    assert isinstance(out["quotes"], list)
    assert "strip" in out["macro"]


def test_holdings_pulse_quotes_exist() -> None:
    from monitors import holdings_monitor

    payload = holdings_monitor.run_holdings_pulse(["QQQ"])
    assert "quotes" in payload
