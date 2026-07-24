from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from data_layer.cache import JsonTTLCache

logger = logging.getLogger(__name__)

ROOT_DEFAULT = Path(__file__).resolve().parents[1]


@dataclass
class PositionRow:
    ticker: str
    qty: float
    avg_cost_per_share: float

    @property
    def cost_basis_usd(self) -> float:
        return round(self.qty * self.avg_cost_per_share, 4)


def load_account_cash_usd(watchlist_path: Path | None = None) -> float:
    """
    Optional cash balance mirrored from broker statement.
    Supported keys in watchlist.yaml: account_cash_usd / cash_usd / cash
    """
    wp = watchlist_path or (ROOT_DEFAULT / "config" / "watchlist.yaml")
    wl = yaml.safe_load(wp.read_text(encoding="utf-8")) or {}
    for k in ("account_cash_usd", "cash_usd", "cash"):
        v = wl.get(k)
        if v is None:
            continue
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            continue
    return 0.0


def load_positions(watchlist_path: Path | None = None) -> list[PositionRow]:
    wp = watchlist_path or (ROOT_DEFAULT / "config" / "watchlist.yaml")
    wl = yaml.safe_load(wp.read_text(encoding="utf-8"))
    out: list[PositionRow] = []
    for row in wl.get("symbols") or []:
        tk = str(row.get("ticker") or "").upper().strip()
        if not tk:
            continue
        q_raw = row.get("qty")
        if q_raw is None:
            continue
        qty = float(q_raw)
        avg = row.get("avg_cost_usd_per_share")
        legacy_total = row.get("cost_basis_total_usd") or row.get("cost_basis_usd_total")
        if avg is None and legacy_total is not None and qty > 0:
            avg = float(legacy_total) / qty
        elif avg is not None:
            avg = float(avg)
        else:
            avg = 0.0
        out.append(PositionRow(ticker=tk, qty=qty, avg_cost_per_share=avg))
    return out


def sector_for_symbol(sym: str, *, ttl_seconds: int = 86_400) -> str:
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return "Offline"

    cache = JsonTTLCache("yf_sectors", ttl_seconds)
    ck = cache.key(sym.upper())
    hit = cache.get(ck)
    if isinstance(hit, str) and hit:
        return hit

    label = "Unknown"
    try:
        import yfinance as yf  # noqa: PLC0415

        info = yf.Ticker(sym).info or {}
        label = (
            info.get("sector")
            or info.get("quoteType")
            or info.get("category")
            or info.get("industry")
            or "Unknown"
        )
        if not isinstance(label, str):
            label = str(label)
    except Exception:
        logger.debug("sector fetch failed %s", sym, exc_info=True)

    cache.set(ck, label)
    return label


def equity_curve_daily(symbol_qty: dict[str, float], *, lookback_calendar_days: int = 45) -> pd.Series:
    """Σ(qty × adj. close); returns last ≤30 observations (trading dates)."""

    if os.environ.get("ALPHAWATCH_OFFLINE") == "1" or not symbol_qty:
        return pd.Series(dtype=float)

    cache = JsonTTLCache("portfolio_equity_curve_v1", int(os.environ.get("ALPHA_CURVE_CACHE_TTL", "300")))
    ck = cache.key(
        *[f"{s}:{symbol_qty[s]:.6f}".rstrip("0").rstrip(".") for s in sorted(symbol_qty)],
        str(lookback_calendar_days),
    )
    blob = cache.get(ck)
    if isinstance(blob, list) and blob:
        try:
            idx = pd.to_datetime([row["d"] for row in blob])
            out = pd.Series([float(row["v"]) for row in blob], index=idx).sort_index()
            return out.tail(30)
        except (KeyError, TypeError, ValueError):
            pass

    cols: dict[str, pd.Series] = {}
    try:
        import yfinance as yf  # noqa: PLC0415

        for raw, qty in symbol_qty.items():
            sym = raw.strip().upper()
            h = yf.Ticker(sym).history(
                period=f"{lookback_calendar_days}d",
                interval="1d",
                auto_adjust=True,
            )
            if h is None or h.empty:
                continue
            s = (h["Close"].astype(float) * qty).copy()
            if s.index.tz is not None:
                s.index = s.index.tz_localize(None)
            cols[sym] = s
    except Exception:
        logger.warning("equity_curve_daily yfinance failure", exc_info=True)

    if not cols:
        return pd.Series(dtype=float)

    df = pd.concat(cols, axis=1).sort_index().ffill()
    equity = df.sum(axis=1).dropna()
    tail = equity.tail(30)

    blob_out = [{"d": str(ix.date()), "v": float(v)} for ix, v in tail.items()]
    try:
        cache.set(ck, blob_out)
    except Exception:
        pass

    return tail.astype(float)
