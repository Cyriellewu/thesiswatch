"""SEC EDGAR 8-K fetcher."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from news_pipeline.fetchers.base import BaseFetcher, RawNews

log = logging.getLogger(__name__)

SEC_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "AlphaWatch personal-research contact@example.com")


class SECEdgarFetcher(BaseFetcher):
    name = "sec_edgar"
    rate_limit_seconds = 0.15

    def __init__(self, user_agent: str = SEC_USER_AGENT):
        self.user_agent = user_agent
        self._ticker_to_cik: dict[str, str] = {}
        self._load_ticker_cik_map()

    def _load_ticker_cik_map(self) -> None:
        try:
            resp = requests.get(SEC_TICKER_CIK_URL, headers={"User-Agent": self.user_agent}, timeout=10)
            resp.raise_for_status()
            for item in resp.json().values():
                self._ticker_to_cik[str(item["ticker"]).upper()] = str(item["cik_str"]).zfill(10)
        except Exception as exc:
            log.warning("[sec] failed to load ticker-CIK map: %s", exc)

    def fetch(self, tickers: list[str], days_back: int = 7) -> list[RawNews]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        all_news: list[RawNews] = []
        for ticker in tickers:
            cik = self._ticker_to_cik.get(ticker.upper())
            if not cik:
                continue
            all_news.extend(self._fetch_8k_for_cik(ticker.upper(), cik, cutoff))
            time.sleep(self.rate_limit_seconds)
        return all_news

    def _fetch_8k_for_cik(self, ticker: str, cik: str, cutoff: datetime) -> list[RawNews]:
        try:
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                headers={"User-Agent": self.user_agent},
                timeout=15,
            )
            resp.raise_for_status()
            recent = resp.json().get("filings", {}).get("recent", {})
        except Exception as exc:
            log.warning("[sec] %s fetch failed: %s", ticker, exc)
            return []
        out: list[RawNews] = []
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            try:
                filing_date = datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if filing_date < cutoff:
                    continue
                accession = accessions[i].replace("-", "")
                desc = descriptions[i] if i < len(descriptions) else "8-K Filing"
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary_docs[i]}"
                out.append(
                    RawNews(
                        title=f"{ticker} 8-K: {desc or '重大事件公告'}",
                        url=filing_url,
                        source="sec_edgar:8-K",
                        published_at=filing_date,
                        summary=desc,
                        primary_ticker=ticker,
                        affected_tickers=[ticker],
                        raw_payload={"cik": cik, "accession": accession, "form": "8-K"},
                    )
                )
            except Exception:
                log.debug("[sec] %s skip malformed entry", ticker, exc_info=True)
        return out
