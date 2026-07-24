"""Auto-evidence — turn today's computed signals into dated thesis evidence.

Bridges the signal layer (conviction factors, news attention, guru overlap,
concentration flags) into the structured Thesis Ledger, so a thesis's confidence
and Delta reflect *today's facts* without the user hand-entering every data
point. Also derives which invalidation conditions are literally triggered.

Pure and offline: takes already-computed dicts, returns Evidence objects + a
signals map. No network, no LLM.
"""
from __future__ import annotations

import re
from typing import Any

from tasks.thesis import Evidence

# invalidation keyword -> a checker over the stock/advice facts.
# The condition text must contain the keyword for a hit (see thesis.invalidation_hits).
_MA200_RE = re.compile(r"200\s*日|200d|200-day|长期均线|生命线", re.IGNORECASE)
_RSI_RE = re.compile(r"rsi", re.IGNORECASE)


def evidence_from_signals(
    stock: dict[str, Any] | None,
    focus_entry: dict[str, Any] | None,
    guru_overlap: list[dict[str, Any]] | None,
    in_news: bool,
    concentration_flags: list[str] | None = None,
) -> list[Evidence]:
    """Derive auto-evidence for one ticker from today's signals. Each item is
    source-tagged 'auto:<kind>' so it's distinguishable from user evidence."""
    out: list[Evidence] = []
    s = stock or {}

    # 1) Conviction factors -> one evidence each (support if +, counter if -).
    for fac in (focus_entry or {}).get("factors", []):
        delta = float(fac.get("delta", 0) or 0)
        if delta == 0:
            continue
        stance = "support" if delta > 0 else "counter"
        out.append(Evidence(
            text=f"{fac.get('label','')}：{fac.get('detail','')}",
            kind="price" if "RSI" in str(fac.get("label")) or "动量" in str(fac.get("label")) or "位置" in str(fac.get("label")) else "fundamental",
            stance=stance,
            source="auto:conviction",
            weight=min(3.0, abs(delta) / 5.0 + 0.5),
        ))

    # 2) Smart-money overlap -> supporting, low-frequency evidence (design note:
    #    guru 13F is lagged, so weight is modest and kind is smart_money).
    for g in (guru_overlap or []):
        pct = float(g.get("pct") or 0.0)
        who = str(g.get("label", "")).split(" (")[0]
        out.append(Evidence(
            text=f"{who} 持有（占其组合 {pct:.1f}%）",
            kind="smart_money", stance="support", source="auto:guru",
            weight=min(1.5, 0.5 + pct / 20.0),
        ))

    # 3) News attention -> neutral-ish support (attention only, never a claim).
    if in_news:
        out.append(Evidence(
            text="今日出现在相关新闻中（需人工判断利好/利空）",
            kind="news", stance="support", source="auto:news", weight=0.5,
        ))

    # 4) Concentration -> a counter/risk evidence when this name is over-weighted.
    for flag in (concentration_flags or []):
        if str(s.get("ticker", "")).upper() and str(s.get("ticker", "")).upper() in flag:
            out.append(Evidence(
                text=f"集中度提示：{flag}", kind="macro", stance="counter",
                source="auto:concentration", weight=1.0,
            ))
    return out


def signals_for_invalidation(stock: dict[str, Any] | None) -> dict[str, Any]:
    """Build a keyword->triggered map so InvestmentThesis.invalidation_hits can
    check conditions against real facts. Conservative: only sets a key True when
    the fact is clearly present."""
    s = stock or {}
    sig: dict[str, Any] = {}
    action = str(s.get("action", ""))
    reason = str(s.get("reason_zh", ""))
    pos52 = s.get("pos_52")
    rsi = s.get("rsi")

    # 200-day line break: the rule engine's reason often says so; also low 52w.
    if _MA200_RE.search(reason) or ("200" in reason):
        sig["200日"] = True
        sig["200d"] = True
        sig["长期均线"] = True
    if action in ("Avoid for now",):
        sig["跌破"] = True
    if isinstance(pos52, (int, float)) and pos52 <= 10:
        sig["年内低"] = True
        sig["新低"] = True
    if isinstance(rsi, (int, float)) and rsi <= 30:
        sig["rsi"] = True
        sig["超卖"] = True
    return sig
