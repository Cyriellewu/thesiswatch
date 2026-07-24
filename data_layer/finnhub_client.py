from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE = "https://finnhub.io/api/v1"


def is_configured() -> bool:
    return bool(os.environ.get("FINNHUB_TOKEN", "").strip())


def _token() -> str:
    t = os.environ.get("FINNHUB_TOKEN", "").strip()
    if not t:
        raise RuntimeError("FINNHUB_TOKEN is not set")
    return t


def fetch_earnings_calendar_rows(
    symbols: list[str],
    *,
    horizon_days: int = 60,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Call GET /calendar/earnings for a date window, then keep only watchlist tickers.

    Docs: https://finnhub.io/docs/api/stock-earnings-calendar
    """
    want = {s.strip().upper() for s in symbols if s and s.strip()}
    if not want:
        return []

    today = date.today()
    end = today + timedelta(days=horizon_days)
    params = {
        "from": today.isoformat(),
        "to": end.isoformat(),
        "token": _token(),
    }
    r = requests.get(f"{BASE}/calendar/earnings", params=params, timeout=timeout)
    if r.status_code == 403:
        logger.warning("FinnHub returned 403 — check token or endpoint access on your plan.")
        return []
    r.raise_for_status()
    data = r.json() or {}
    rows_out: list[dict[str, Any]] = []
    for row in data.get("earningsCalendar") or []:
        sym = str(row.get("symbol") or "").upper()
        if sym not in want:
            continue
        d_raw = row.get("date") or row.get("quarterEnd")
        if not d_raw or not isinstance(d_raw, str):
            continue
        day = d_raw[:10]
        rows_out.append(
            {
                "symbol": sym,
                "report_date": day,
                "hour": row.get("hour"),
                "eps_estimate": row.get("epsEstimate"),
                "revenue_estimate": row.get("revenueEstimate"),
            }
        )

    rows_out.sort(key=lambda x: x["report_date"])
    return rows_out


def fetch_company_news(
    symbol: str,
    *,
    days_back: int = 7,
    days_future: int = 0,
    timeout: float = 25.0,
) -> list[dict[str, Any]]:
    """
    GET /company-news — 请配合 `data_layer.cache` 做节流，避免打满免费档。
    """
    today = date.today()
    frm = today - timedelta(days=days_back)
    to = today + timedelta(days=days_future)
    r = requests.get(
        f"{BASE}/company-news",
        params={
            "symbol": symbol.strip().upper(),
            "from": frm.isoformat(),
            "to": to.isoformat(),
            "token": _token(),
        },
        timeout=timeout,
    )
    if not r.ok:
        logger.warning("FinnHub company-news HTTP %s for %s", r.status_code, symbol)
        return []
    payload = r.json()
    return payload if isinstance(payload, list) else []


def fetch_recommendation_trends(
    symbol: str,
    *,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """GET /stock/recommendation, newest first."""
    r = requests.get(
        f"{BASE}/stock/recommendation",
        params={"symbol": symbol.strip().upper(), "token": _token()},
        timeout=timeout,
    )
    if not r.ok:
        logger.warning("FinnHub recommendation HTTP %s for %s", r.status_code, symbol)
        return []
    payload = r.json()
    rows = payload if isinstance(payload, list) else []
    rows.sort(key=lambda x: str((x or {}).get("period") or ""), reverse=True)
    return rows


def fetch_insider_transactions(
    symbol: str,
    *,
    days_back: int = 30,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """GET /stock/insider-transactions (change can be + buy / - sell)."""
    today = date.today()
    frm = today - timedelta(days=max(1, int(days_back)))
    r = requests.get(
        f"{BASE}/stock/insider-transactions",
        params={
            "symbol": symbol.strip().upper(),
            "from": frm.isoformat(),
            "to": today.isoformat(),
            "token": _token(),
        },
        timeout=timeout,
    )
    if not r.ok:
        logger.warning("FinnHub insider-transactions HTTP %s for %s", r.status_code, symbol)
        return []
    payload = r.json() or {}
    rows = payload.get("data") if isinstance(payload, dict) else []
    return rows if isinstance(rows, list) else []
