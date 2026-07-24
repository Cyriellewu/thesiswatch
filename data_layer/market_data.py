from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from data_layer.cache import JsonTTLCache

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    symbol: str
    px: float
    chg_pct: float


def _load_symbols() -> list[str]:
    p = Path(__file__).resolve().parents[1] / "config" / "watchlist.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    symbols: list[str] = []
    for row in data.get("symbols") or []:
        t = (row.get("ticker") or "").upper()
        if t:
            symbols.append(t)
    return symbols


def _demo_quotes(symbols: list[str]) -> list[Quote]:
    out: list[Quote] = []
    for idx, sym in enumerate(symbols):
        base = 100 + idx * 3.71
        chg = ((idx % 7) - 3) * 0.62
        out.append(Quote(symbol=sym.upper(), px=round(base, 2), chg_pct=round(chg, 3)))
    return out


def fetch_quotes_demo(symbols: list[str] | None = None, ttl_seconds: int = 300) -> list[Quote]:
    symbols = symbols or _load_symbols()
    cache = JsonTTLCache("market_quotes_demo", ttl_seconds)
    ck = cache.key(*symbols)
    hit = cache.get(ck)
    if hit:
        return [Quote(**row) for row in hit]

    out = _demo_quotes(symbols)
    cache.set(ck, [vars(q) for q in out])
    return out


def _yf_quote(sym: str) -> Quote | None:
    try:
        import yfinance as yf  # noqa: PLC0415

        h = yf.Ticker(sym).history(period="10d", interval="1d", auto_adjust=True)
        if h is None or h.empty:
            logger.debug("No yfinance bars for %s", sym)
            return None
        if len(h.index) < 2:
            last = float(h["Close"].iloc[-1])
            return Quote(symbol=sym.upper(), px=round(last, 4), chg_pct=0.0)
        last = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        if prev == 0:
            return None
        chg_pct = round((last - prev) / prev * 100, 3)
        return Quote(symbol=sym.upper(), px=round(last, 4), chg_pct=chg_pct)
    except Exception:
        logger.debug("yfinance failed for %s", sym, exc_info=True)
        return None


def fetch_quotes(symbols: list[str] | None = None, ttl_seconds: int | None = None) -> list[Quote]:
    """
    Live quotes via yfinance + cache; fallback per symbol to deterministic demo rows.
    Set ALPHAWATCH_OFFLINE=1 to skip Yahoo.
    Default cache TTL 60s (override with ALPHA_QUOTE_CACHE_TTL or pass ttl_seconds).
    """

    symbols = symbols or _load_symbols()
    cache_ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else int(os.environ.get("ALPHA_QUOTE_CACHE_TTL", "60"))
    )
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return fetch_quotes_demo(symbols, cache_ttl)

    cache = JsonTTLCache("market_quotes_yf", cache_ttl)
    ck = cache.key(*sorted(symbols))
    hit = cache.get(ck)
    if hit:
        return [Quote(**row) for row in hit]

    demos = {q.symbol.upper(): q for q in _demo_quotes(symbols)}
    out: list[Quote] = []
    for sym in symbols:
        u = sym.strip().upper()
        q = _yf_quote(u)
        out.append(q if q else (demos.get(u) or Quote(symbol=u, px=0.0, chg_pct=0.0)))

    cache.set(ck, [vars(q) for q in out])
    return out
