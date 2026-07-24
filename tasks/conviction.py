"""Multi-factor conviction scoring — the agentic "what's worth watching today".

Fuses the signals AlphaWatch already computes per stock (rule-based action, RSI,
52-week position, today's move) with two context signals (smart-money / guru
holdings, and today's news attention) into a single 0-100 conviction score, a
direction, the contributing factors, and a one-line Chinese thesis.

Pure and offline: `gurus` and `news` are optional and the score degrades
gracefully when they're absent. No network, no side effects.
"""
from __future__ import annotations

from typing import Any, Iterable

# Actions the rule engine emits, mapped to a directional lean in [-1, 1].
_ACTION_LEAN = {
    "Buy small": 0.6,
    "Add on weakness": 0.8,
    "Normal DCA": 0.4,
    "Hold": 0.0,
    "Wait for confirmation": -0.1,
    "Do not chase": -0.4,
    "Avoid for now": -0.7,
    "Trim": -0.6,
    "Take profit": -0.5,
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _guru_index(gurus: Iterable[dict[str, Any]] | None) -> dict[str, float]:
    """Map ticker -> summed % weight across gurus (a smart-money conviction proxy)."""
    idx: dict[str, float] = {}
    for g in gurus or []:
        for h in g.get("holdings") or []:
            sym = str(h.get("symbol", "")).upper()
            if not sym:
                continue
            idx[sym] = idx.get(sym, 0.0) + float(h.get("pct") or 0.0)
    return idx


def _news_tickers(news: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    if not news:
        return out
    for item in news.get("holdings") or []:
        tk = str(item.get("ticker", "")).upper()
        if tk:
            out.add(tk)
    return out


def score_stock(
    stock: dict[str, Any],
    guru_pct: float = 0.0,
    guru_count: int = 0,
    in_news: bool = False,
    vol_20d: float | None = None,
) -> dict[str, Any]:
    """Return a conviction dict for one stock. Base 50, factors nudge it."""
    factors: list[dict[str, Any]] = []
    score = 50.0

    # 1) Rule-based action lean (the TA verdict already computed upstream).
    action = stock.get("action", "Hold")
    lean = _ACTION_LEAN.get(action, 0.0)
    if lean:
        delta = lean * 18.0
        score += delta
        factors.append({
            "label": "技术信号",
            "detail": stock.get("action_zh", action),
            "delta": round(delta, 1),
        })

    # 2) RSI: oversold is opportunity, overbought is caution.
    rsi = stock.get("rsi")
    if isinstance(rsi, (int, float)):
        if rsi <= 32:
            score += 10
            factors.append({"label": "RSI 超卖", "detail": f"RSI {rsi:.0f}，回调到位", "delta": 10})
        elif rsi >= 72:
            score -= 10
            factors.append({"label": "RSI 超买", "detail": f"RSI {rsi:.0f}，短线偏热", "delta": -10})

    # 3) 52-week position: near lows = value zone; near highs = extended.
    pos52 = stock.get("pos_52")
    if isinstance(pos52, (int, float)):
        if pos52 <= 25:
            score += 8
            factors.append({"label": "接近年内低位", "detail": f"52周位置 {pos52:.0f}%", "delta": 8})
        elif pos52 >= 90:
            score -= 6
            factors.append({"label": "接近年内高位", "detail": f"52周位置 {pos52:.0f}%", "delta": -6})

    # 4) Momentum (today's move), capped so a single day can't dominate.
    chg = float(stock.get("chg_pct") or 0.0)
    mom = _clamp(chg * 1.5, -9, 9)
    if abs(mom) >= 2:
        score += mom
        factors.append({
            "label": "今日动量",
            "detail": f"{chg:+.1f}%",
            "delta": round(mom, 1),
        })

    # 5) Smart money: gurus hold it, weighted by summed % and breadth.
    if guru_pct > 0:
        boost = _clamp(guru_pct * 1.2, 0, 14) + _clamp(guru_count * 1.5, 0, 6)
        score += boost
        who = f"{guru_count} 位大佬合计约 {guru_pct:.0f}%" if guru_count else f"约 {guru_pct:.0f}%"
        factors.append({"label": "大佬持仓", "detail": who, "delta": round(boost, 1)})

    # 6) News attention today.
    if in_news:
        score += 4
        factors.append({"label": "今日有新闻", "detail": "出现在今日要闻中", "delta": 4})

    # 7) Volatility penalty: high 20-day annualized vol is riskier for a retail
    # holder, so it shaves conviction (honest risk adjustment).
    if isinstance(vol_20d, (int, float)) and vol_20d > 0:
        if vol_20d >= 60:
            score -= 8
            factors.append({"label": "高波动", "detail": f"20日年化波动 {vol_20d:.0f}%", "delta": -8})
        elif vol_20d >= 40:
            score -= 4
            factors.append({"label": "波动偏高", "detail": f"20日年化波动 {vol_20d:.0f}%", "delta": -4})

    score = round(_clamp(score, 0, 100), 1)

    if score >= 68:
        direction = "重点关注"
    elif score >= 56:
        direction = "偏多留意"
    elif score >= 44:
        direction = "中性观望"
    else:
        direction = "谨慎回避"

    return {
        "ticker": stock.get("ticker", ""),
        "conviction": score,
        "direction": direction,
        "factors": factors,
        "action_zh": stock.get("action_zh", ""),
        "chg_pct": chg,
        "pnl_pct": stock.get("pnl_pct"),
        "weight_pct": stock.get("weight_pct", 0.0),
        "thesis": _thesis(stock, score, direction, factors),
    }


def _thesis(stock: dict[str, Any], score: float, direction: str, factors: list[dict[str, Any]]) -> str:
    tk = stock.get("ticker", "")
    top = "、".join(f["label"] for f in sorted(factors, key=lambda x: abs(x["delta"]), reverse=True)[:3])
    if not top:
        return f"{tk}：信号平淡（{score:.0f} 分），{direction}。"
    return f"{tk}：{direction}（{score:.0f} 分）——主要因为 {top}。"


def build_focus(
    stocks: list[dict[str, Any]],
    gurus: list[dict[str, Any]] | None = None,
    news: dict[str, Any] | None = None,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Score every stock and return the top-N by conviction (most worth watching)."""
    guru_idx = _guru_index(gurus)
    guru_breadth: dict[str, int] = {}
    for g in gurus or []:
        for h in g.get("holdings") or []:
            sym = str(h.get("symbol", "")).upper()
            if sym:
                guru_breadth[sym] = guru_breadth.get(sym, 0) + 1
    news_tk = _news_tickers(news)

    scored = [
        score_stock(
            s,
            guru_pct=guru_idx.get(s.get("ticker", "").upper(), 0.0),
            guru_count=guru_breadth.get(s.get("ticker", "").upper(), 0),
            in_news=s.get("ticker", "").upper() in news_tk,
            vol_20d=s.get("vol_20d"),
        )
        for s in stocks
    ]
    scored.sort(key=lambda x: x["conviction"], reverse=True)
    return scored[:top_n]
