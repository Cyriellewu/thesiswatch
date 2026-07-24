from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from data_layer.cache import JsonTTLCache
from data_layer.fred_client import latest_observation

logger = logging.getLogger(__name__)


@dataclass
class MacroStrip:
    vix: float
    ten_year_yield_pct: float
    dxy: float
    spy_chg_pct: float
    qqq_chg_pct: float
    crude_chg_pct: float = 0.0


@dataclass(frozen=True)
class MacroStripView:
    """UI 专用：始终含 crude_chg_pct，避免旧版 MacroStrip 或热缓存缺字段。"""

    vix: float
    ten_year_yield_pct: float
    dxy: float
    spy_chg_pct: float
    qqq_chg_pct: float
    crude_chg_pct: float


def snapshot_macro_demo() -> MacroStrip:
    return MacroStrip(
        vix=24.3,
        ten_year_yield_pct=4.31,
        dxy=105.2,
        spy_chg_pct=0.3,
        qqq_chg_pct=0.5,
        crude_chg_pct=0.0,
    )


def _etf_daily_chg(symbol: str) -> float | None:
    """Previous close vs prior close approximates last session daily % change."""

    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return None
    try:
        import yfinance as yf  # noqa: PLC0415

        h = yf.Ticker(symbol).history(period="7d", interval="1d", auto_adjust=True)
        if h is None or len(h.index) < 2:
            return None
        last = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        if prev == 0:
            return None
        return round((last - prev) / prev * 100, 3)
    except Exception:
        logger.debug("yfinance macro fetch failed for %s", symbol, exc_info=True)
        return None


def _yahoo_last_close(ticker: str) -> float | None:
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return None
    try:
        import yfinance as yf  # noqa: PLC0415

        h = yf.Ticker(ticker).history(period="7d", interval="1d", auto_adjust=True)
        if h is None or h.empty:
            return None
        return float(h["Close"].iloc[-1])
    except Exception:
        return None


def snapshot_macro(*, ttl_seconds: int = 300) -> MacroStrip:
    """
    Prefer FRED (VIXCLS, DGS10, DTWEXBGS) + yfinance SPY/QQQ moves.
    Set ALPHAWATCH_OFFLINE=1 to force placeholders.
    """

    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return snapshot_macro_demo()

    cache = JsonTTLCache("macro_strip", ttl_seconds)
    ck = "strip_v2_dxy"
    hit = cache.get(ck)
    if hit:
        return _macrostrip_new(
            vix=float((hit or {}).get("vix") or snapshot_macro_demo().vix),
            ten_year=float((hit or {}).get("ten_year_yield_pct") or snapshot_macro_demo().ten_year_yield_pct),
            dxy=float((hit or {}).get("dxy") or snapshot_macro_demo().dxy),
            spy=float((hit or {}).get("spy_chg_pct") or snapshot_macro_demo().spy_chg_pct),
            qqq=float((hit or {}).get("qqq_chg_pct") or snapshot_macro_demo().qqq_chg_pct),
            crude=float((hit or {}).get("crude_chg_pct") or 0.0),
        )

    vix = snapshot_macro_demo().vix
    ten_year = snapshot_macro_demo().ten_year_yield_pct
    dxy = snapshot_macro_demo().dxy

    vix_obs = latest_observation("VIXCLS")
    if vix_obs:
        vix = float(vix_obs["value"])

    y10 = latest_observation("DGS10")
    if y10:
        ten_year = float(y10["value"])

    # Use the market DXY ticker for the UI label "DXY". FRED DTWEXBGS is a
    # broad dollar index and often prints around 115-125, which confused the
    # dashboard because it is not the familiar DXY range.
    dx = _yahoo_last_close("DX-Y.NYB") or _yahoo_last_close("^DXY")
    if dx is not None:
        dxy = dx
    else:
        dxy_obs = latest_observation(os.environ.get("FRED_DXY_SERIES", "DTWEXBGS")) if os.environ.get("FRED_DXY_SERIES") else None
        if dxy_obs:
            dxy = float(dxy_obs["value"])

    spy_c = _etf_daily_chg("SPY")
    qqq_c = _etf_daily_chg("QQQ")
    crude_c = _etf_daily_chg("CL=F")

    if vix_obs is None:
        vx = _yahoo_last_close("^VIX")
        if vx is not None:
            vix = vx

    if not (70 <= float(dxy) <= 115):
        # Last guardrail: if a broad-dollar or stale value slipped through,
        # show the conservative demo DXY instead of a misleading 118-style DXY.
        fallback_dx = _yahoo_last_close("^DXY")
        dxy = fallback_dx if fallback_dx is not None and 70 <= float(fallback_dx) <= 115 else snapshot_macro_demo().dxy

    if y10 is None:
        tnx = _yahoo_last_close("^TNX")
        if tnx is not None:
            ten_year = float(tnx) / 100.0 if float(tnx) > 25 else float(tnx)

    strip = _macrostrip_new(
        vix=round(float(vix), 2),
        ten_year=round(float(ten_year), 3),
        dxy=round(float(dxy), 2),
        spy=spy_c if spy_c is not None else snapshot_macro_demo().spy_chg_pct,
        qqq=qqq_c if qqq_c is not None else snapshot_macro_demo().qqq_chg_pct,
        crude=crude_c if crude_c is not None else snapshot_macro_demo().crude_chg_pct,
    )
    cache.set(ck, vars(strip))
    return strip


def _macrostrip_new(*, vix: float, ten_year: float, dxy: float, spy: float, qqq: float, crude: float) -> MacroStrip:
    """
    Backward-compatible ctor:
    some running sessions may still hold an older MacroStrip class object
    that does not accept `crude_chg_pct`.
    """
    try:
        return MacroStrip(
            vix=float(vix),
            ten_year_yield_pct=float(ten_year),
            dxy=float(dxy),
            spy_chg_pct=float(spy),
            qqq_chg_pct=float(qqq),
            crude_chg_pct=float(crude),
        )
    except TypeError:
        return MacroStrip(
            vix=float(vix),
            ten_year_yield_pct=float(ten_year),
            dxy=float(dxy),
            spy_chg_pct=float(spy),
            qqq_chg_pct=float(qqq),
        )


def snapshot_macro_view(*, ttl_seconds: int = 300) -> MacroStripView:
    """
    给 Streamlit / 展示层用：无论底层 MacroStrip 是否带 crude_chg_pct，都返回完整视图。
    若底层缺油价涨跌，会尽量补一次 CL=F（与 snapshot_macro 缓存无关）。
    """
    m = snapshot_macro(ttl_seconds=ttl_seconds)
    crude = getattr(m, "crude_chg_pct", None)
    if crude is None:
        crude_f = _etf_daily_chg("CL=F")
        crude = crude_f if crude_f is not None else 0.0
    return MacroStripView(
        vix=float(getattr(m, "vix", 0.0) or 0.0),
        ten_year_yield_pct=float(getattr(m, "ten_year_yield_pct", 0.0) or 0.0),
        dxy=float(getattr(m, "dxy", 0.0) or 0.0),
        spy_chg_pct=float(getattr(m, "spy_chg_pct", 0.0) or 0.0),
        qqq_chg_pct=float(getattr(m, "qqq_chg_pct", 0.0) or 0.0),
        crude_chg_pct=float(crude or 0.0),
    )
