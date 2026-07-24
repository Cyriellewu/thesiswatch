from __future__ import annotations

from data_layer.portfolio_analytics import sector_for_symbol


def classify_sector(symbol: str) -> str:
    sym = str(symbol or "").upper().strip()
    if not sym:
        return "Unknown"
    sec = sector_for_symbol(sym)
    sec = str(sec or "Unknown").strip()
    return sec or "Unknown"

