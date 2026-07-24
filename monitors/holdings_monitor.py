from __future__ import annotations

from data_layer.market_data import fetch_quotes
from data_layer.news_aggregator import holdings_news_compact


def run_holdings_pulse(symbols: list[str]) -> dict:
    """Return a concise payload for dashboards / routers."""

    quotes = fetch_quotes(symbols)
    headlines = holdings_news_compact(symbols)
    return {
        "quotes": [vars(q) for q in quotes],
        "headlines_min": [{"symbol": h.symbol, "title": h.headline} for h in headlines[:16]],
    }
