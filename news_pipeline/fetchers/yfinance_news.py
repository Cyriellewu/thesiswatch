"""Yahoo Finance news fetcher."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import yfinance as yf

from news_pipeline.fetchers.base import BaseFetcher, RawNews

log = logging.getLogger(__name__)


class YFinanceNewsFetcher(BaseFetcher):
    name = "yfinance"
    rate_limit_seconds = 0.5

    def fetch(self, tickers: list[str]) -> list[RawNews]:
        all_news: list[RawNews] = []
        for ticker in tickers:
            all_news.extend(self._fetch_one(ticker.upper()))
            time.sleep(self.rate_limit_seconds)
        return all_news

    def _fetch_one(self, ticker: str) -> list[RawNews]:
        try:
            rows = yf.Ticker(ticker).news or []
        except Exception as exc:
            log.warning("[yfinance] %s fetch failed: %s", ticker, exc)
            return []
        out: list[RawNews] = []
        for item in rows:
            try:
                content = item.get("content") or item
                title = str(content.get("title") or "").strip()
                url = (
                    (content.get("canonicalUrl") or {}).get("url")
                    or (content.get("clickThroughUrl") or {}).get("url")
                    or content.get("link")
                    or ""
                )
                pub_str = content.get("pubDate") or content.get("displayTime")
                if pub_str:
                    try:
                        published = datetime.fromisoformat(str(pub_str).replace("Z", "+00:00"))
                    except Exception:
                        published = datetime.now(timezone.utc)
                else:
                    published = datetime.fromtimestamp(item.get("providerPublishTime", time.time()), tz=timezone.utc)
                provider = content.get("provider")
                provider_name = provider.get("displayName", "yahoo") if isinstance(provider, dict) else "yahoo"
                out.append(
                    RawNews(
                        title=title,
                        url=str(url).strip(),
                        source=f"yfinance:{provider_name}",
                        published_at=published,
                        summary=str(content.get("summary") or "").strip(),
                        primary_ticker=ticker,
                        affected_tickers=[ticker],
                    )
                )
            except Exception:
                log.debug("[yfinance] %s skip malformed", ticker, exc_info=True)
        return [n for n in out if n.title and n.url]
