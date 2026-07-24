"""News pipeline orchestrator."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # noqa: SIM105
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from db.client import default_db_path  # noqa: E402
from news_pipeline.deduper import dedupe_and_cluster  # noqa: E402
from news_pipeline.fetchers.finnhub import FinnhubFetcher  # noqa: E402
from news_pipeline.fetchers.base import ensure_utc  # noqa: E402
from news_pipeline.fetchers.reuters import ReutersFetcher  # noqa: E402
from news_pipeline.fetchers.sec_edgar import SECEdgarFetcher  # noqa: E402
from news_pipeline.fetchers.yfinance_news import YFinanceNewsFetcher  # noqa: E402
from news_pipeline.storage import init_news_schema, upsert_news  # noqa: E402

log = logging.getLogger(__name__)


def run_news_pipeline(
    db_path: str | Path | None = None,
    watchlist_tickers: list[str] | None = None,
    days_back: int = 1,
    sources: list[str] | None = None,
) -> dict:
    from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers  # noqa: PLC0415

    db = Path(db_path) if db_path is not None else default_db_path()
    tickers = watchlist_tickers or [*load_watchlist_tickers(), *load_opportunity_tickers()]
    tickers = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    init_news_schema(db)

    enabled = {"finnhub", "reuters", "sec_edgar", "yfinance"} if sources is None else set(sources)
    all_news = []
    by_source: dict[str, int] = {}

    if "finnhub" in enabled and (os.environ.get("FINNHUB_TOKEN") or os.environ.get("FINNHUB_API_KEY")):
        fetcher = FinnhubFetcher()
        rows = fetcher.safe_fetch(tickers=tickers, days_back=days_back)
        all_news.extend(rows)
        by_source["finnhub"] = len(rows)

    if "reuters" in enabled:
        fetcher = ReutersFetcher(watchlist_tickers=tickers)
        rows = fetcher.safe_fetch()
        all_news.extend(rows)
        by_source["reuters"] = len(rows)

    if "sec_edgar" in enabled:
        fetcher = SECEdgarFetcher()
        rows = fetcher.safe_fetch(tickers=tickers, days_back=days_back * 7)
        all_news.extend(rows)
        by_source["sec_edgar"] = len(rows)

    if "yfinance" in enabled:
        fetcher = YFinanceNewsFetcher()
        rows = fetcher.safe_fetch(tickers=tickers)
        all_news.extend(rows)
        by_source["yfinance"] = len(rows)

    for item in all_news:
        item.published_at = ensure_utc(item.published_at)
    representatives, cluster_map = dedupe_and_cluster(all_news)
    inserted = upsert_news(db, representatives, cluster_map)
    return {
        "fetched": len(all_news),
        "after_dedupe": len(representatives),
        "inserted": inserted,
        "by_source": by_source,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else None
    print(run_news_pipeline(path, days_back=3))
