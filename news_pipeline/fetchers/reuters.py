"""Reuters RSS fetcher."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import feedparser

from news_pipeline.fetchers.base import BaseFetcher, RawNews

log = logging.getLogger(__name__)

REUTERS_FEEDS = {
    "business": "https://feeds.reuters.com/reuters/businessNews",
    "markets": "https://feeds.reuters.com/reuters/USMarketsNews",
    "tech": "https://feeds.reuters.com/reuters/technologyNews",
    "world": "https://feeds.reuters.com/reuters/worldNews",
}
TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")


class ReutersFetcher(BaseFetcher):
    name = "reuters"
    rate_limit_seconds = 2.0

    def __init__(self, watchlist_tickers: list[str] | None = None):
        self.watchlist = {t.upper() for t in (watchlist_tickers or [])}

    def fetch(self, categories: list[str] | None = None) -> list[RawNews]:
        cats = categories or ["business", "markets", "tech"]
        all_news: list[RawNews] = []
        for cat in cats:
            url = REUTERS_FEEDS.get(cat)
            if url:
                all_news.extend(self._parse_feed(url, cat))
        return all_news

    def _parse_feed(self, url: str, category: str) -> list[RawNews]:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            log.warning("[reuters] %s parse failed: %s", category, exc)
            return []
        out: list[RawNews] = []
        for entry in feed.entries:
            try:
                title = str(entry.get("title") or "").strip()
                summary = str(entry.get("summary") or "").strip()
                link = str(entry.get("link") or "").strip()
                if getattr(entry, "published_parsed", None):
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                else:
                    published = datetime.now(timezone.utc)
                affected = self._extract_tickers(f"{title} {summary}")
                out.append(
                    RawNews(
                        title=title,
                        url=link,
                        source=f"reuters:{category}",
                        published_at=published,
                        summary=summary,
                        primary_ticker=affected[0] if affected else None,
                        affected_tickers=affected,
                    )
                )
            except Exception:
                log.debug("[reuters] skip malformed entry", exc_info=True)
        return [n for n in out if n.title and n.url]

    def _extract_tickers(self, text: str) -> list[str]:
        found = {t for t in TICKER_RE.findall(text) if len(t) <= 5}
        if self.watchlist:
            return sorted(found & self.watchlist)
        return sorted(found)
