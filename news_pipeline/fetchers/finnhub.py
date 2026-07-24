"""FinnHub company-news fetcher."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from news_pipeline.fetchers.base import BaseFetcher, RawNews

log = logging.getLogger(__name__)


class FinnhubFetcher(BaseFetcher):
    name = "finnhub"
    rate_limit_seconds = 1.1
    base_url = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FINNHUB_TOKEN") or os.environ.get("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("FINNHUB_TOKEN / FINNHUB_API_KEY not set")

    def fetch(self, tickers: list[str], days_back: int = 3) -> list[RawNews]:
        all_news: list[RawNews] = []
        to_date = datetime.now().date()
        from_date = to_date - timedelta(days=days_back)
        for ticker in tickers:
            all_news.extend(self._fetch_one(ticker, from_date, to_date))
            time.sleep(self.rate_limit_seconds)
        return all_news

    def _fetch_one(self, ticker: str, from_date, to_date) -> list[RawNews]:
        try:
            resp = requests.get(
                f"{self.base_url}/company-news",
                params={
                    "symbol": ticker.upper(),
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "token": self.api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("[finnhub] %s fetch failed: %s", ticker, exc)
            return []
        if not isinstance(data, list):
            return []
        out: list[RawNews] = []
        for item in data:
            try:
                published = datetime.fromtimestamp(item["datetime"], tz=timezone.utc)
                related = [t.strip().upper() for t in str(item.get("related") or "").split(",") if t.strip()]
                affected = [ticker.upper(), *[t for t in related if t != ticker.upper()]]
                out.append(
                    RawNews(
                        title=str(item.get("headline") or "").strip(),
                        url=str(item.get("url") or "").strip(),
                        source=f"finnhub:{item.get('source', 'unknown')}",
                        published_at=published,
                        summary=str(item.get("summary") or "").strip(),
                        primary_ticker=ticker.upper(),
                        affected_tickers=affected,
                        image_url=item.get("image") or None,
                        raw_payload=item,
                    )
                )
            except Exception:
                log.debug("[finnhub] skip malformed item", exc_info=True)
        return [n for n in out if n.title and n.url]
