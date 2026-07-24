"""Thesis explain — the data behind the 'Why Changed' sheet and Evidence Sheet.

Two pure helpers:
- factor_delta(): diff two conviction factor sets into the "71 → 78 because
  +4 Azure, -2 capex" attribution the Why-Changed sheet renders.
- classify_evidence(): split a thesis's evidence into Fact / Model
  interpretation / Assumption / Counterargument buckets, compute freshness, and
  detect support-vs-counter conflict — the Evidence Sheet's honesty layer.

Pure and offline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from tasks.thesis import Evidence, InvestmentThesis


def factor_delta(
    before_factors: list[dict[str, Any]] | None,
    after_factors: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Diff conviction factor lists (each {label, detail, delta}). Returns the
    point movement plus new / removed / changed factors, sorted by impact."""
    b = {f.get("label", ""): float(f.get("delta", 0) or 0) for f in (before_factors or [])}
    a_detail = {f.get("label", ""): f.get("detail", "") for f in (after_factors or [])}
    a = {f.get("label", ""): float(f.get("delta", 0) or 0) for f in (after_factors or [])}

    rows: list[dict[str, Any]] = []
    for label in set(b) | set(a):
        bv, av = b.get(label), a.get(label)
        if bv is None:
            kind = "new"
            move = av
        elif av is None:
            kind = "removed"
            move = -bv
        else:
            move = av - bv
            kind = "changed" if abs(move) >= 0.5 else "same"
        rows.append({
            "label": label,
            "detail": a_detail.get(label, ""),
            "before": bv,
            "after": av,
            "move": round(move or 0.0, 1),
            "kind": kind,
        })
    # Sort by absolute movement, biggest mover first; drop the "same" noise.
    movers = [r for r in rows if r["kind"] != "same"]
    movers.sort(key=lambda r: abs(r["move"]), reverse=True)
    return {
        "before_total": round(sum(b.values()), 1),
        "after_total": round(sum(a.values()), 1),
        "movers": movers,
    }


# Evidence-kind → which honesty bucket it belongs in.
_FACT_KINDS = {"price", "fundamental", "macro"}          # measured/observed data
_INTERP_KINDS = {"news", "smart_money"}                  # needs interpretation
_USER_KINDS = {"user"}                                   # user's own assumption


def classify_evidence(thesis: InvestmentThesis, *, now: datetime | None = None) -> dict[str, Any]:
    """Bucket evidence into Fact / Interpretation / Assumption / Counterargument,
    attach freshness, and flag support-vs-counter conflict."""
    facts: list[dict[str, Any]] = []
    interpretations: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    counters: list[dict[str, Any]] = []

    support_w = 0.0
    counter_w = 0.0

    for e in thesis.evidence:
        item = _ev_item(e, now=now)
        if e.stance == "counter":
            counters.append(item)
            counter_w += e.weight
            continue
        support_w += e.weight
        if e.kind in _USER_KINDS:
            assumptions.append(item)
        elif e.kind in _INTERP_KINDS:
            interpretations.append(item)
        else:
            facts.append(item)

    conflict = support_w > 0 and counter_w > 0
    return {
        "facts": facts,
        "interpretations": interpretations,
        "assumptions": assumptions,
        "counterarguments": counters,
        "conflict": conflict,
        "support_sources": sum(1 for e in thesis.evidence if e.stance == "support"),
        "counter_sources": sum(1 for e in thesis.evidence if e.stance == "counter"),
    }


def _ev_item(e: Evidence, *, now: datetime | None = None) -> dict[str, Any]:
    age = e.age_days(now=now)
    if age is None:
        fresh = "未知"
    elif age <= 1:
        fresh = "今天"
    elif age <= 7:
        fresh = f"{age:.0f} 天前"
    elif age <= 45:
        fresh = f"{age:.0f} 天前（偏旧）"
    else:
        fresh = f"{age:.0f} 天前（过期，谨慎采信）"
    return {
        "text": e.text,
        "kind": e.kind,
        "stance": e.stance,
        "source": e.source,
        "weight": e.weight,
        "freshness": fresh,
        "age_days": age,
        "auto": str(e.source).startswith("auto:"),
    }
