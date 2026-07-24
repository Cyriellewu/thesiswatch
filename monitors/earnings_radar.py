from __future__ import annotations

from data_layer.earnings_data import upcoming_earnings


def run_earnings_radar(symbols: list[str]) -> list[dict]:
    out = []
    for e in upcoming_earnings(symbols):
        d = vars(e).copy()
        d["report_date"] = e.report_date.isoformat()
        out.append(d)
    return out
