from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

logger = logging.getLogger(__name__)


@dataclass
class EarningsEvent:
    symbol: str
    report_date: date
    sentiment_hint: str | None = None


def upcoming_earnings_stub(symbols: list[str]) -> list[EarningsEvent]:
    """Offline placeholder when `FINNHUB_TOKEN` is missing or the API errors."""

    today = date.today()
    return [
        EarningsEvent(symbol=s, report_date=today + timedelta(days=3 + idx), sentiment_hint=None)
        for idx, s in enumerate(symbols[:5])
    ]


def upcoming_earnings(symbols: list[str]) -> list[EarningsEvent]:
    """
    Prefer FinnHub `/calendar/earnings` when `FINNHUB_TOKEN` is set; otherwise stub.
    """
    from data_layer.finnhub_client import fetch_earnings_calendar_rows, is_configured

    if not is_configured():
        return upcoming_earnings_stub(symbols)

    try:
        rows = fetch_earnings_calendar_rows(symbols)
        if not rows:
            logger.info("FinnHub calendar returned no rows for watchlist; falling back to stub.")
            return upcoming_earnings_stub(symbols)
        deduped: dict[tuple[str, str], EarningsEvent] = {}
        for row in rows:
            sym = str(row["symbol"]).upper()
            d = date.fromisoformat(row["report_date"])
            deduped[(sym, row["report_date"])] = EarningsEvent(symbol=sym, report_date=d, sentiment_hint=None)
        return sorted(deduped.values(), key=lambda e: e.report_date)
    except Exception:
        logger.exception("FinnHub earnings calendar failed; using stub.")
        return upcoming_earnings_stub(symbols)
