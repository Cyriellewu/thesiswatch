"""Portfolio exposure — hidden common-factor risk + what-if simulation.

Answers the locked Portfolio question: "my portfolio looks diversified, but what
common risks am I actually betting on?" Maps each holding to shared theme
factors (AI infrastructure, mega-cap tech, semiconductors, rate sensitivity,
China policy) and aggregates weighted exposure. A pure what-if simulator
recomputes exposures/concentration for hypothetical trades — no auto-trading.

Pure and offline. Theme membership is a transparent, editable map (not a model
guess), so the analysis is explainable and honest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Transparent theme membership: ticker -> {theme: membership 0..1}.
# Editable/extendable; absence means no known exposure (not a guess).
_THEME_MAP: dict[str, dict[str, float]] = {
    "NVDA": {"AI 基础设施": 1.0, "半导体": 1.0, "大型科技": 0.6},
    "AVGO": {"AI 基础设施": 0.8, "半导体": 1.0, "大型科技": 0.5},
    "AMD": {"AI 基础设施": 0.7, "半导体": 1.0},
    "TSM": {"半导体": 1.0, "AI 基础设施": 0.6, "中国/地缘": 0.5},
    "MU": {"半导体": 1.0},
    "SMH": {"半导体": 1.0, "AI 基础设施": 0.6},
    "SOXX": {"半导体": 1.0, "AI 基础设施": 0.6},
    "MSFT": {"AI 基础设施": 0.7, "大型科技": 1.0},
    "GOOGL": {"AI 基础设施": 0.5, "大型科技": 1.0},
    "AMZN": {"大型科技": 1.0, "AI 基础设施": 0.4},
    "AAPL": {"大型科技": 1.0, "中国/地缘": 0.4},
    "META": {"大型科技": 1.0, "AI 基础设施": 0.4},
    "TSLA": {"大型科技": 0.6, "中国/地缘": 0.4},
    "QQQ": {"大型科技": 0.7, "AI 基础设施": 0.4, "半导体": 0.25},
    "VOO": {"大型科技": 0.3, "利率敏感": 0.2},
    "SPY": {"大型科技": 0.3, "利率敏感": 0.2},
    "IREN": {"AI 基础设施": 0.8, "加密/算力": 1.0},
    "CRWV": {"AI 基础设施": 1.0},
    "SMR": {"核能/电力": 1.0, "利率敏感": 0.4},
    "BE": {"核能/电力": 0.7, "利率敏感": 0.4},
}

_LEVEL = [(40, "高"), (20, "中"), (0, "低")]


def _level(pct: float) -> str:
    for thresh, label in _LEVEL:
        if pct >= thresh:
            return label
    return "低"


@dataclass
class ThemeExposure:
    theme: str
    pct: float                       # weighted % of portfolio
    level: str
    contributors: list[dict[str, Any]] = field(default_factory=list)  # [{ticker, weight_pct, contribution}]


def compute_exposures(positions: list[dict[str, Any]]) -> list[ThemeExposure]:
    """positions = [{ticker, weight_pct}]. Returns weighted theme exposures,
    highest first. Theme % = sum(weight_pct * membership) over holdings."""
    theme_pct: dict[str, float] = {}
    theme_contrib: dict[str, list[dict[str, Any]]] = {}
    for p in positions:
        tk = str(p.get("ticker", "")).upper()
        w = float(p.get("weight_pct") or 0.0)
        if not tk or w <= 0:
            continue
        for theme, member in _THEME_MAP.get(tk, {}).items():
            contribution = w * member
            theme_pct[theme] = theme_pct.get(theme, 0.0) + contribution
            theme_contrib.setdefault(theme, []).append(
                {"ticker": tk, "weight_pct": round(w, 1), "contribution": round(contribution, 1)}
            )
    out = [
        ThemeExposure(
            theme=theme,
            pct=round(pct, 1),
            level=_level(pct),
            contributors=sorted(theme_contrib[theme], key=lambda c: c["contribution"], reverse=True),
        )
        for theme, pct in theme_pct.items()
    ]
    out.sort(key=lambda e: e.pct, reverse=True)
    return out


def simulate(
    positions: list[dict[str, Any]],
    cash: float,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply hypothetical trades and recompute weights + exposures.

    positions = [{ticker, weight_pct, mv}] (mv = market value $).
    changes = [{ticker, delta_usd}] (+add / -reduce). Cash adjusts inversely.
    Returns before/after exposures + concentration, without mutating inputs.
    """
    # Rebuild market values.
    mv = {str(p.get("ticker", "")).upper(): float(p.get("mv") or 0.0) for p in positions}
    new_cash = float(cash)
    for ch in changes:
        tk = str(ch.get("ticker", "")).upper()
        d = float(ch.get("delta_usd") or 0.0)
        mv[tk] = max(0.0, mv.get(tk, 0.0) + d)
        new_cash = max(0.0, new_cash - d)

    total = sum(mv.values())
    after_positions = [
        {"ticker": tk, "weight_pct": (v / total * 100.0) if total > 0 else 0.0, "mv": v}
        for tk, v in mv.items() if v > 0
    ]

    before_exp = compute_exposures(positions)
    after_exp = compute_exposures(after_positions)

    def _top_concentration(ps: list[dict[str, Any]]) -> float:
        return round(max((p["weight_pct"] for p in ps), default=0.0), 1)

    tot_with_cash = total + new_cash
    return {
        "before_exposures": [e.__dict__ for e in before_exp],
        "after_exposures": [e.__dict__ for e in after_exp],
        "before_top_weight": _top_concentration(positions),
        "after_top_weight": _top_concentration(after_positions),
        "after_cash_pct": round((new_cash / tot_with_cash * 100.0) if tot_with_cash > 0 else 0.0, 1),
        "after_positions": after_positions,
    }


def known_themes() -> list[str]:
    seen: list[str] = []
    for m in _THEME_MAP.values():
        for t in m:
            if t not in seen:
                seen.append(t)
    return seen
