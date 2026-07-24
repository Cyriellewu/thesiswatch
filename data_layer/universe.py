from __future__ import annotations

from pathlib import Path

import yaml

from data_layer.watchlist_resolver import watchlist_path

_REPO = Path(__file__).resolve().parents[1]


def load_watchlist_tickers(path: Path | None = None) -> list[str]:
    p = path or watchlist_path()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: list[str] = []
    for row in raw.get("symbols") or []:
        tk = str(row.get("ticker") or "").upper().strip()
        if tk:
            out.append(tk)
    return out


def load_opportunity_tickers(path: Path | None = None) -> list[str]:
    p = path or (_REPO / "config" / "opportunity_universe.yaml")
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = raw.get("tickers") or []
    out: list[str] = []
    for row in rows:
        if isinstance(row, str):
            sym = row.upper().strip()
        else:
            sym = str((row or {}).get("symbol") or "").upper().strip()
        if sym:
            out.append(sym)
    return out


def pulse_symbol_universe() -> list[str]:
    """持仓 ∪ 机会池，去重；用于一次抓取、统一信号。"""

    seen: dict[str, None] = {}
    merged = [*load_watchlist_tickers(), *load_opportunity_tickers()]
    for sym in merged:
        if sym not in seen:
            seen[sym] = None
    return list(seen.keys())
