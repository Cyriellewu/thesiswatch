from __future__ import annotations

from dataclasses import dataclass

from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from data_layer.portfolio_analytics import sector_for_symbol


@dataclass
class StockSnapshot:
    symbol: str
    name: str
    sector: str
    country: str
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    earnings_stability_score: float | None = None
    debt_to_equity: float | None = None
    revenue_growth_5y: float | None = None


def load_base_stock_universe() -> list[StockSnapshot]:
    syms = sorted(set(load_watchlist_tickers()) | set(load_opportunity_tickers()))
    out: list[StockSnapshot] = []
    for s in syms:
        out.append(
            StockSnapshot(
                symbol=s,
                name=s,
                sector=str(sector_for_symbol(s) or "Unknown"),
                country="US",
            )
        )
    return out

