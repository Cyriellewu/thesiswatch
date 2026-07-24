from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def test_weekly_dca_friday_fallback_when_no_dip() -> None:
    from tasks.weekly_dca import run_weekly_dca_check

    friday_1500 = datetime(2026, 7, 3, 15, 30, tzinfo=NY)

    class FakeQuote:
        def __init__(self, symbol: str, px: float, chg_pct: float):
            self.symbol = symbol
            self.px = px
            self.chg_pct = chg_pct

    with patch("tasks.weekly_dca.is_equity_trading_day", return_value=True), patch(
        "tasks.weekly_dca.fetch_quotes",
        return_value=[FakeQuote("QQQ", 500.0, 0.3), FakeQuote("VOO", 550.0, 0.1)],
    ), patch("tasks.weekly_dca.send_weekly_dca_recommendation", return_value=True) as send_mock, patch(
        "db.client.get_conn"
    ) as get_conn_mock:
        conn = get_conn_mock.return_value
        conn.execute.return_value.fetchone.return_value = None
        stats = run_weekly_dca_check(friday_1500)

    assert stats["pushed"] == 2
    assert send_mock.call_count == 2
    triggers = {c.kwargs["symbol"]: c.kwargs["trigger"] for c in send_mock.call_args_list}
    assert triggers["QQQ"] == "friday_fallback"
    assert triggers["VOO"] == "friday_fallback"


def test_weekly_dca_dip_trigger_before_friday() -> None:
    from tasks.weekly_dca import run_weekly_dca_check

    wednesday = datetime(2026, 7, 1, 11, 0, tzinfo=NY)

    class FakeQuote:
        def __init__(self, symbol: str, px: float, chg_pct: float):
            self.symbol = symbol
            self.px = px
            self.chg_pct = chg_pct

    with patch("tasks.weekly_dca.is_equity_trading_day", return_value=True), patch(
        "tasks.weekly_dca.fetch_quotes",
        return_value=[FakeQuote("QQQ", 490.0, -0.8), FakeQuote("VOO", 545.0, 0.2)],
    ), patch("tasks.weekly_dca.send_weekly_dca_recommendation", return_value=True) as send_mock, patch(
        "db.client.get_conn"
    ) as get_conn_mock:
        conn = get_conn_mock.return_value
        conn.execute.return_value.fetchone.return_value = None
        stats = run_weekly_dca_check(wednesday)

    assert stats["pushed"] == 1
    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["symbol"] == "QQQ"
    assert send_mock.call_args.kwargs["trigger"] == "dip"


def test_news_tagger_validates_stance() -> None:
    from news_pipeline.news_tagger import _validate_tag

    tag = {
        "id": "x1",
        "category": "product",
        "severity": "important",
        "one_line_zh": "Meta 进军云计算",
        "impact_on_holdings": "科技仓受益",
        "market_stance": "bullish",
        "what_it_means_zh": "Meta 对标 AWS，估值模型重构，利好。",
    }
    assert _validate_tag(tag) is True
    assert tag["market_stance"] == "bullish"

    bad = dict(tag)
    bad["market_stance"] = "maybe_up"
    assert _validate_tag(bad) is True
    assert bad["market_stance"] == "neutral"
