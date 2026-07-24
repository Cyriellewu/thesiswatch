"""Typed API envelope (ADR-001) — the honesty-bearing shell for every /api/* response.

Pure module: imports only the standard library and pydantic (no pandas, no engine,
no FastAPI), so the envelope shape and the honest-state derivation are unit-testable
offline and in isolation from the heavy engine runtime.

Contract (ADR-001):
    { data, state: ok|stale|partial|unavailable, mode: demo|live,
      observed_at, fetched_at, sources[], warnings[] }

`state` and the timestamps are DERIVED from real source records. Unknown values become
`null` plus a warning — never a `datetime.now()` stand-in (the bug this replaces).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

EnvelopeState = Literal["ok", "stale", "partial", "unavailable"]
EnvelopeMode = Literal["demo", "live"]

# The engine stamps as_of in Singapore time ("%Y-%m-%d %H:%M"); keep the offset explicit
# so the ISO timestamp we expose is unambiguous rather than a naive local string.
_SGT = timezone(timedelta(hours=8))


class ApiEnvelope(BaseModel, Generic[T]):
    """Every /api/* payload is wrapped in this. FastAPI enforces it via response_model."""

    data: Optional[T] = None
    state: EnvelopeState
    mode: EnvelopeMode
    observed_at: Optional[str] = None
    fetched_at: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def current_mode() -> EnvelopeMode:
    """Explicit Demo vs Live (ADR-004). Demo when the offline/demo flag is set; the mode
    is always reported to the client so it never has to guess what it is looking at."""
    if os.environ.get("ALPHAWATCH_MODE", "").strip().lower() == "demo":
        return "demo"
    if os.environ.get("ALPHAWATCH_OFFLINE", "").strip() in ("1", "true", "True"):
        return "demo"
    return "live"


def parse_sgt(as_of: str) -> Optional[str]:
    """Convert the engine's `as_of` ("YYYY-MM-DD HH:MM", SGT) into an ISO-8601 string.
    Returns None (not a fabricated time) when the value is missing or unparseable."""
    try:
        dt = datetime.strptime(as_of.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=_SGT)
        return dt.isoformat()
    except (ValueError, AttributeError):
        return None


def today_meta(advice: dict[str, Any]) -> dict[str, Any]:
    """Derive honest envelope metadata for /api/today from a real advice dict.

    Honesty rules:
    - `fetched_at` is the engine's real compute time (`as_of`); None if unknown.
    - `observed_at` is None because per-source observation time is not tracked yet
      (ADR-007 is a later slice); we say so in a warning rather than faking `now()`.
    - `state`: unavailable if there are no holdings or every holding fell back to a
      stale/fallback quote; partial if some did; ok otherwise. ("stale" is reserved for
      when real per-source ages exist.)
    """
    stocks = advice.get("stocks") or []
    total = len(stocks)
    degraded = int(advice.get("data_degraded") or 0)
    source = str(advice.get("source") or "rule_based")

    fetched_at = parse_sgt(str(advice.get("as_of", "")))

    warnings: list[str] = []
    if total == 0:
        state: EnvelopeState = "unavailable"
        warnings.append("No holdings could be priced; the daily answer is unavailable.")
    elif degraded >= total:
        state = "unavailable"
        warnings.append(
            f"All {total} holdings used fallback quotes (live snapshot unavailable)."
        )
    elif degraded > 0:
        state = "partial"
        warnings.append(
            f"{degraded} of {total} holdings used fallback quotes (live snapshot unavailable)."
        )
    else:
        state = "ok"

    if fetched_at is None:
        warnings.append("Engine compute time was unavailable; fetched_at is null.")
    warnings.append("Per-source observation time is not tracked yet; observed_at is null.")

    return {
        "state": state,
        "fetched_at": fetched_at,
        "observed_at": None,
        "sources": [source],
        "warnings": warnings,
    }


def unavailable_meta(reason: str) -> dict[str, Any]:
    """Envelope metadata for a hard failure — explicit `unavailable`, never a silent
    empty-but-ok response."""
    return {
        "state": "unavailable",
        "fetched_at": None,
        "observed_at": None,
        "sources": [],
        "warnings": [reason],
    }
