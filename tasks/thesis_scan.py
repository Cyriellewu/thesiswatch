"""Thesis daily scan — orchestrates the "Today" answer.

Ties the pieces together for a portfolio: for each holding, auto-derive today's
evidence from signals, merge onto the stored thesis, diff vs the previous
snapshot, classify into No action / Watch / Re-evaluate, and roll up into an
honest portfolio headline (including the "no action needed today" case).

Reads the private thesis store; writes today's snapshot back so tomorrow's diff
has a 'before'. Degrades gracefully: holdings without a saved thesis still get a
signal-only status.
"""
from __future__ import annotations

from typing import Any

from tasks.thesis import InvestmentThesis
from tasks.thesis_autoevidence import evidence_from_signals, signals_for_invalidation
from tasks.thesis_delta import diff_thesis
from tasks.thesis_status import ThesisStatus, classify, summarize_portfolio


def _merge_auto_evidence(base: InvestmentThesis, auto: list) -> InvestmentThesis:
    """Return a copy of `base` with auto-evidence merged in (dedup by id, and
    replace any prior auto:* evidence so today's signals supersede yesterday's)."""
    kept = [e for e in base.evidence if not str(e.source).startswith("auto:")]
    merged = InvestmentThesis(
        ticker=base.ticker, claims=list(base.claims), catalysts=list(base.catalysts),
        risks=list(base.risks), invalidation_conditions=list(base.invalidation_conditions),
        evidence=kept + list(auto), horizon=base.horizon,
    )
    return merged


def scan_holding(
    advice: dict[str, Any],
    ticker: str,
    *,
    gurus: list[dict[str, Any]] | None = None,
    persist: bool = False,
    now: Any | None = None,
) -> ThesisStatus:
    """Compute today's thesis status for one ticker."""
    from tasks import thesis_store as store  # local import keeps module pure-ish

    tk = ticker.upper()
    stock = next((s for s in advice.get("stocks", []) if str(s.get("ticker", "")).upper() == tk), None)
    focus_entry = next((f for f in advice.get("focus", []) if str(f.get("ticker", "")).upper() == tk), None)
    flags = (advice.get("portfolio", {}) or {}).get("concentration_flags", []) or []
    news_tk = {str(i.get("ticker", "")).upper() for i in (advice.get("news", {}) or {}).get("holdings", [])}
    overlap = None
    if gurus is not None:
        overlap = []
        for g in gurus:
            for h in g.get("holdings") or []:
                if str(h.get("symbol", "")).upper() == tk:
                    overlap.append({"label": g.get("label", ""), "pct": float(h.get("pct") or 0.0)})

    auto = evidence_from_signals(stock, focus_entry, overlap, tk in news_tk, flags)
    signals = signals_for_invalidation(stock)

    base = store.load_thesis(tk) or InvestmentThesis(ticker=tk)
    prev = store.load_thesis(tk)  # current stored snapshot = the "before" for diff
    merged = _merge_auto_evidence(base, auto)

    delta = diff_thesis(prev, merged, signals=signals, now=now)
    status = classify(merged if merged.coverage() > 0 else None, delta, now=now)

    if persist:
        store.save_thesis(merged)
        # Log to decision history on material status/confidence changes.
        try:
            from tasks import decision_history as dh  # noqa: PLC0415

            price = float((stock or {}).get("px") or 0.0) or None
            dh.record_if_changed(tk, status.status, status.confidence.get("score", 0.0),
                                 status.reasons, price)
        except Exception:
            pass
    return status


def scan_portfolio(
    advice: dict[str, Any],
    *,
    gurus: list[dict[str, Any]] | None = None,
    persist: bool = False,
    now: Any | None = None,
):
    """Scan every holding and roll up into the Today summary."""
    holdings = [s.get("ticker") for s in advice.get("stocks", []) if s.get("ticker")]
    statuses = [scan_holding(advice, tk, gurus=gurus, persist=persist, now=now) for tk in holdings]
    return summarize_portfolio(statuses), statuses


def brief_text(portfolio_status, statuses) -> str:
    """One honest paragraph for the daily push — 'No action needed today' when
    truly nothing changed at thesis level."""
    lines = [f"{portfolio_status.emoji} {portfolio_status.headline_zh}"]
    for s in portfolio_status.needs_attention[:3]:
        lines.append(f"🟠 {s.ticker}：{'；'.join(s.reasons[:2])}")
    for s in portfolio_status.worth_watching[:3]:
        lines.append(f"🟡 {s.ticker}：{'；'.join(s.reasons[:1])}")
    if portfolio_status.overall == "no_action":
        lines.append("（无需为短期波动做动作。逻辑有变时我会提醒你。）")
    return "\n".join(lines)
