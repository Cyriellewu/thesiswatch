from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from data_layer.cache import JsonTTLCache

logger = logging.getLogger(__name__)


@dataclass
class Article:
    headline: str
    url: str | None
    symbol: str | None
    published_at: datetime
    vendor: str


def _hash_article(a: Article) -> str:
    base = "|".join(
        [
            a.headline.strip().lower(),
            (a.symbol or "").upper(),
            a.url or "",
        ]
    )
    return hashlib.sha256(base.encode()).hexdigest()


def merge_and_deduplicate(entries: list[Article]) -> list[Article]:
    seen: dict[str, Article] = {}
    ordered: list[Article] = []
    for a in entries:
        hid = _hash_article(a)
        if hid in seen:
            continue
        seen[hid] = a
        ordered.append(a)
    ordered.sort(key=lambda x: x.published_at.timestamp(), reverse=True)
    return ordered


def rss_stub_holdings_news(symbols: list[str]) -> list[Article]:
    now = datetime.now(timezone.utc)
    out: list[Article] = []
    for idx, sym in enumerate(symbols[:5]):
        out.append(
            Article(
                headline=f"{sym} · demo headline {(idx % 4) + 1}",
                url=None,
                symbol=sym,
                published_at=now,
                vendor="rss_stub",
            )
        )
    return merge_and_deduplicate(out)


def _demo_news_enabled() -> bool:
    return os.environ.get("ALPHA_NEWS_DEMO", "").strip() == "1"


def _fallback_holdings_news(symbols: list[str]) -> list[Article]:
    return rss_stub_holdings_news(symbols) if _demo_news_enabled() else []


def _finnhub_row_to_article(row: dict, symbol: str) -> Article | None:
    headline = (row.get("headline") or "").strip()
    if not headline:
        return None
    url = row.get("url")
    ts_raw = row.get("datetime")
    if isinstance(ts_raw, (int, float)):
        published = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
    elif isinstance(ts_raw, str) and ts_raw.isdigit():
        published = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
    else:
        published = datetime.now(timezone.utc)
    return Article(headline=headline, url=url, symbol=symbol.upper(), published_at=published, vendor="finnhub")


def holdings_news_fused(
    symbols: list[str],
    *,
    ttl_seconds: int | None = None,
    max_symbols: int | None = None,
    per_fetch: int = 8,
) -> list[Article]:
    """
    FinnHub company news when `FINNHUB_TOKEN` is set (cached per symbol);
    returns no news when offline or FinnHub unavailable unless ALPHA_NEWS_DEMO=1.
    """
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return _fallback_holdings_news(symbols)

    from data_layer import finnhub_client  # noqa: PLC0415

    ttl = ttl_seconds if ttl_seconds is not None else int(os.environ.get("CACHE_TTL_NEWS", "900"))
    capped = list(symbols) if max_symbols is None else symbols[:max_symbols]

    if not finnhub_client.is_configured():
        return _fallback_holdings_news(capped)

    cache = JsonTTLCache("finnhub_company_news", ttl)
    pooled: list[Article] = []
    limit_n = max(1, min(per_fetch, 30))

    for sym in capped:
        s = sym.strip().upper()
        if not s:
            continue
        ck = cache.key(s)
        blob = cache.get(ck)
        if blob:
            for raw in blob:
                d = dict(raw)
                d["published_at"] = datetime.fromisoformat(str(d["published_at"]))
                pooled.append(Article(**d))
            continue

        batch: list[Article] = []
        try:
            rows = finnhub_client.fetch_company_news(s)
            rows = rows[:limit_n]
            for row in rows:
                a = _finnhub_row_to_article(row if isinstance(row, dict) else {}, s)
                if a:
                    batch.append(a)
        except Exception:
            logger.warning("FinnHub company-news failed for %s", s, exc_info=True)

        cache.set(ck, [{**vars(a), "published_at": a.published_at.isoformat()} for a in batch])
        pooled.extend(batch)

    merged = merge_and_deduplicate(pooled)
    if not merged:
        return _fallback_holdings_news(capped)
    return merged[:72]


def holdings_news_compact(symbols: list[str]) -> list[Article]:
    cap = int(os.environ.get("NEWS_FINNHUB_SYMBOL_CAP", "12"))
    return holdings_news_fused(symbols, max_symbols=cap)
