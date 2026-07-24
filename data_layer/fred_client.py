from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(os.environ.get("FRED_API_KEY", "").strip())


def latest_observation(series_id: str, *, timeout: float = 20.0) -> dict[str, Any] | None:
    """Latest non-missing observation for a FRED series."""

    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return None

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 15,
    }
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params=params,
            timeout=timeout,
        )
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("observations") or []
        for row in rows:
            raw = row.get("value")
            if raw is None or raw == ".":
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            return {"date": row.get("date"), "value": val}
    except requests.RequestException:
        logger.warning("FRED request failed for %s", series_id, exc_info=True)
    except (KeyError, TypeError, ValueError):
        logger.warning("FRED parse failed for %s", series_id, exc_info=True)
    return None
