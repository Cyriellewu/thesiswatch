"""Thesis store — local, private persistence for the thesis ledger.

Stored as a single JSON file under `data/` (gitignored, like watchlist.yaml), so
a user's investment theses never enter git. Keeps the *previous* snapshot per
ticker so Thesis Delta can answer "what changed since last time".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tasks.thesis import InvestmentThesis

_REPO = Path(__file__).resolve().parents[1]
_STORE = _REPO / "data" / "thesis_ledger.json"


def _read_raw() -> dict[str, Any]:
    if not _STORE.exists():
        return {"theses": {}, "history": {}}
    try:
        return json.loads(_STORE.read_text(encoding="utf-8")) or {"theses": {}, "history": {}}
    except (OSError, ValueError):
        return {"theses": {}, "history": {}}


def _write_raw(data: dict[str, Any]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STORE)


def load_thesis(ticker: str) -> InvestmentThesis | None:
    raw = _read_raw()
    d = raw.get("theses", {}).get(ticker.upper())
    return InvestmentThesis.from_dict(d) if d else None


def load_previous(ticker: str) -> InvestmentThesis | None:
    """The prior snapshot (before the most recent save), for delta computation."""
    raw = _read_raw()
    d = raw.get("history", {}).get(ticker.upper())
    return InvestmentThesis.from_dict(d) if d else None


def all_tickers() -> list[str]:
    return sorted(_read_raw().get("theses", {}).keys())


def save_thesis(thesis: InvestmentThesis) -> None:
    """Persist a thesis, moving the existing current snapshot into history so
    the next diff has a 'before' to compare against."""
    tk = thesis.ticker.upper()
    raw = _read_raw()
    theses = raw.setdefault("theses", {})
    history = raw.setdefault("history", {})
    if tk in theses:
        history[tk] = theses[tk]           # demote current -> previous
    thesis.updated_at = datetime.now(timezone.utc).isoformat()
    theses[tk] = thesis.to_dict()
    _write_raw(raw)


def delete_thesis(ticker: str) -> None:
    raw = _read_raw()
    tk = ticker.upper()
    raw.get("theses", {}).pop(tk, None)
    raw.get("history", {}).pop(tk, None)
    _write_raw(raw)


def store_path() -> Path:
    return _STORE
