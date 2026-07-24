"""Conviction history — daily snapshots of each stock's conviction + factors.

Lets the "Why Changed" view compare *real* day-over-day conviction ("71 -> 78")
against the exact factor set from the prior snapshot, instead of reconstructing
it from evidence. Stored in the private data dir (gitignored).

Pure logic (snapshot shaping, diffing) is separated from I/O so it's testable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
_STORE = _REPO / "data" / "conviction_history.json"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---- pure logic -----------------------------------------------------------


def make_snapshot(focus_entry: dict[str, Any], *, day: str | None = None) -> dict[str, Any]:
    """Shape one focus entry into a compact daily snapshot."""
    return {
        "day": day or _today(),
        "conviction": float(focus_entry.get("conviction", 0.0)),
        "direction": focus_entry.get("direction", ""),
        "factors": [
            {"label": f.get("label", ""), "detail": f.get("detail", ""), "delta": float(f.get("delta", 0) or 0)}
            for f in focus_entry.get("factors", [])
        ],
    }


def latest_before(snapshots: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    """Most recent snapshot strictly before `day` (for a real day-over-day diff)."""
    prior = [s for s in snapshots if s.get("day", "") < day]
    return prior[-1] if prior else None


# ---- I/O ------------------------------------------------------------------


def _read() -> dict[str, list[dict[str, Any]]]:
    if not _STORE.exists():
        return {}
    try:
        return json.loads(_STORE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, list[dict[str, Any]]]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STORE)


def record(ticker: str, focus_entry: dict[str, Any], *, day: str | None = None) -> None:
    """Store today's snapshot for a ticker (one per day; re-recording replaces)."""
    data = _read()
    tk = ticker.upper()
    snaps = data.get(tk, [])
    snap = make_snapshot(focus_entry, day=day)
    snaps = [s for s in snaps if s.get("day") != snap["day"]]  # replace same-day
    snaps.append(snap)
    snaps.sort(key=lambda s: s.get("day", ""))
    data[tk] = snaps[-90:]  # keep last ~90 days
    _write(data)


def history(ticker: str) -> list[dict[str, Any]]:
    return _read().get(ticker.upper(), [])


def store_path() -> Path:
    return _STORE
