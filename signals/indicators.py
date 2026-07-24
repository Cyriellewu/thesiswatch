"""Daily OHLCV + core indicators (Murphy-style ordering helpers)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import pandas as pd


def _offline() -> bool:
    return os.environ.get("ALPHAWATCH_OFFLINE") == "1"


@lru_cache(maxsize=64)
def load_daily_bars(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    """Return adjusted daily bars with columns: Open, High, Low, Close, Volume (yfinance names)."""
    if _offline():
        return None
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    try:
        import yfinance as yf  # noqa: PLC0415

        h = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
    except Exception:
        return None
    if h is None or h.empty or "Close" not in h.columns:
        return None
    return h


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.astype(float).ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    c = close.astype(float)
    delta = c.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    roll_up = up.ewm(alpha=1 / n, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    c = close.astype(float)
    macd_line = ema(c, fast) - ema(c, slow)
    sig = ema(macd_line, signal)
    hist = macd_line - sig
    return macd_line, sig, hist


def last_value(series: pd.Series | None) -> float | None:
    if series is None or series.empty:
        return None
    v = float(series.iloc[-1])
    return v if v == v else None  # NaN


def bars_summary(h: pd.DataFrame) -> dict[str, Any]:
    """Latest bar + moving averages + RSI + volume context."""
    if h is None or h.empty:
        return {}
    close = h["Close"].astype(float)
    high = h["High"].astype(float)
    low = h["Low"].astype(float)
    vol = h["Volume"].astype(float) if "Volume" in h.columns else close * 0 + 1.0

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    vol50 = vol.rolling(50).mean()
    r = rsi(close, 14)
    _, _, mhist = macd_hist(close)

    last = close.iloc[-1]
    out = {
        "close": float(last),
        "high": float(high.iloc[-1]),
        "low": float(low.iloc[-1]),
        "ma20": last_value(ma20),
        "ma50": last_value(ma50),
        "ma200": last_value(ma200),
        "ma200_slope": float(ma200.iloc[-1] - ma200.iloc[-21]) if len(ma200) > 22 else 0.0,
        "rsi14": last_value(r),
        "macd_hist": last_value(mhist),
        "vol": float(vol.iloc[-1]),
        "vol50": last_value(vol50),
        "ret_63d": float(last / close.iloc[-64] - 1.0) if len(close) > 64 else 0.0,
        "swing_low_20": float(low.tail(20).min()) if len(low) >= 20 else float(low.min()),
        "pivot_20h": float(high.tail(20).max()) if len(high) >= 20 else float(high.max()),
    }
    return out
