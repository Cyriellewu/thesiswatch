from __future__ import annotations

from dataclasses import dataclass

from data_layer.market_data import fetch_quotes
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from data_layer.portfolio_analytics import sector_for_symbol


@dataclass
class Recommendation:
    symbol: str
    name: str
    sector: str
    stability_score: float
    growth_score: float
    valuation_zone: str
    valuation_explanation: str
    current_price: float
    buy_range_text: str
    is_held: bool


_DEF_MAP = {
    "Technology": ("MSFT", "AAPL", "AVGO", "ORCL"),
    "Healthcare": ("LLY", "UNH", "JNJ", "NVO"),
    "Financial Services": ("V", "MA", "JPM", "BRK-B"),
    "Energy": ("XOM", "CVX", "VST", "CEG"),
    "Consumer Defensive": ("COST", "WMT", "PG", "KO"),
    "Utilities": ("NEE", "DUK", "SO", "XLU"),
    "Industrials": ("CAT", "GE", "HON", "ETN"),
}


def _normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 50.0
    z = (x - lo) / (hi - lo)
    return max(0.0, min(100.0, z * 100.0))


def compute_stability_score(symbol: str, sector: str) -> float:
    s = symbol.upper()
    score = 50.0
    if s in {"MSFT", "AAPL", "GOOGL", "NVDA", "V", "MA", "COST", "WMT", "JNJ", "JPM"}:
        score += 25
    if sector in {"Utilities", "Consumer Defensive", "Healthcare"}:
        score += 12
    if s.endswith("Q") or s in {"TSLA", "IREN", "COIN", "OKLO"}:
        score -= 20
    return max(0.0, min(100.0, score))


def compute_growth_score(chg_pct: float, sector: str) -> float:
    base = _normalize(chg_pct, -8.0, 12.0)
    if sector in {"Technology", "Industrials"}:
        base += 8
    return max(0.0, min(100.0, base))


def classify_valuation_zone(current_price: float, proxy_ref_price: float) -> tuple[str, str]:
    if proxy_ref_price <= 0:
        return "fair", "缺少估值参考，暂按合理区处理"
    ratio = current_price / proxy_ref_price
    if ratio <= 0.9:
        return "cheap", "当前价低于参考中枢约 10%+，偏低估区"
    if ratio >= 1.15:
        return "expensive", "当前价高于参考中枢约 15%+，偏贵区"
    return "fair", "当前价在参考中枢附近，属于合理区"


def _candidate_universe() -> list[str]:
    base = set(load_watchlist_tickers()) | set(load_opportunity_tickers())
    for vals in _DEF_MAP.values():
        base |= set(vals)
    return sorted(base)


def generate_recommendation_list(config: dict | None = None) -> list[Recommendation]:
    _ = config
    syms = _candidate_universe()
    held = set(load_watchlist_tickers())
    quotes = fetch_quotes(syms)
    qmap = {q.symbol.upper(): q for q in quotes}
    rows: list[Recommendation] = []

    for sym in syms:
        q = qmap.get(sym)
        if not q or float(q.px or 0.0) <= 0:
            continue
        px = float(q.px)
        chg = float(q.chg_pct or 0.0)
        sector = str(sector_for_symbol(sym) or "Unknown")
        stability = compute_stability_score(sym, sector)
        growth = compute_growth_score(chg, sector)
        ref = px / (1 + (chg / 100.0)) if chg > -95 else px
        zone, explain = classify_valuation_zone(px, ref)
        low = px * 0.92
        high = px * 1.03
        rows.append(
            Recommendation(
                symbol=sym,
                name=sym,
                sector=sector,
                stability_score=round(stability, 1),
                growth_score=round(growth, 1),
                valuation_zone=zone,
                valuation_explanation=explain,
                current_price=round(px, 2),
                buy_range_text=f"${low:,.2f} - ${high:,.2f}",
                is_held=sym in held,
            )
        )

    # sector diversity cap: max 3 each sector
    by_sector: dict[str, list[Recommendation]] = {}
    for r in sorted(rows, key=lambda x: (x.stability_score + x.growth_score), reverse=True):
        by_sector.setdefault(r.sector, []).append(r)
    out: list[Recommendation] = []
    for sec, ls in by_sector.items():
        _ = sec
        out.extend(ls[:3])
    return sorted(out, key=lambda x: (x.stability_score, x.growth_score), reverse=True)

