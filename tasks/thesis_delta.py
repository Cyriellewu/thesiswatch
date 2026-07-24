"""Thesis Delta — diff two thesis snapshots into "what changed since last time".

The product's signature answer: not "the score is 78" but "it went 69 → 78
because two supporting data points arrived and one risk was retired". Pure and
offline; operates on InvestmentThesis objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tasks.thesis import Evidence, InvestmentThesis


@dataclass
class ThesisDelta:
    ticker: str
    direction: str                       # strengthened / weakened / no_material_change
    confidence_before: float
    confidence_after: float
    confidence_change: float
    new_evidence: list[dict[str, Any]] = field(default_factory=list)
    removed_evidence: list[dict[str, Any]] = field(default_factory=list)
    added_risks: list[str] = field(default_factory=list)
    removed_risks: list[str] = field(default_factory=list)
    added_claims: list[str] = field(default_factory=list)
    removed_claims: list[str] = field(default_factory=list)
    invalidation_triggered: list[str] = field(default_factory=list)
    summary_zh: str = ""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _ev_map(t: InvestmentThesis) -> dict[str, Evidence]:
    return {e.id: e for e in t.evidence}


def diff_thesis(
    before: InvestmentThesis | None,
    after: InvestmentThesis,
    *,
    signals: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ThesisDelta:
    """Compute the delta from `before` to `after`. `before` may be None (first
    time we've seen this thesis). `signals` (optional) checks invalidation."""
    conf_after = after.confidence(now=now)["score"]
    conf_before = before.confidence(now=now)["score"] if before else conf_after

    before_ev = _ev_map(before) if before else {}
    after_ev = _ev_map(after)
    new_ids = [i for i in after_ev if i not in before_ev]
    gone_ids = [i for i in before_ev if i not in after_ev]

    before_risks = set(before.risks) if before else set()
    after_risks = set(after.risks)
    before_claims = set(before.claims) if before else set()
    after_claims = set(after.claims)

    triggered = after.invalidation_hits(signals or {})

    change = round(conf_after - conf_before, 1)
    if triggered:
        direction = "weakened"
    elif change >= 3:
        direction = "strengthened"
    elif change <= -3:
        direction = "weakened"
    else:
        direction = "no_material_change"

    delta = ThesisDelta(
        ticker=after.ticker,
        direction=direction,
        confidence_before=conf_before,
        confidence_after=conf_after,
        confidence_change=change,
        new_evidence=[_ev_brief(after_ev[i]) for i in new_ids],
        removed_evidence=[_ev_brief(before_ev[i]) for i in gone_ids],
        added_risks=sorted(after_risks - before_risks),
        removed_risks=sorted(before_risks - after_risks),
        added_claims=sorted(after_claims - before_claims),
        removed_claims=sorted(before_claims - after_claims),
        invalidation_triggered=triggered,
    )
    delta.summary_zh = _summarize(delta, first_time=before is None)
    return delta


def _ev_brief(e: Evidence) -> dict[str, Any]:
    return {"text": e.text, "kind": e.kind, "stance": e.stance, "source": e.source}


def _summarize(d: ThesisDelta, *, first_time: bool) -> str:
    if first_time:
        return f"{d.ticker}：首次建立论点（信心 {d.confidence_after:.0f}）。"
    if d.invalidation_triggered:
        conds = "、".join(d.invalidation_triggered)
        return f"⚠️ {d.ticker}：触发失效条件（{conds}）——原始逻辑可能已被破坏，请复核。"
    if d.direction == "no_material_change":
        return f"{d.ticker}：较上次无实质变化（信心 {d.confidence_after:.0f}）。"
    arrow = "增强" if d.direction == "strengthened" else "减弱"
    bits: list[str] = [f"信心 {d.confidence_before:.0f}→{d.confidence_after:.0f}"]
    if d.new_evidence:
        sup = sum(1 for e in d.new_evidence if e["stance"] == "support")
        con = len(d.new_evidence) - sup
        piece = []
        if sup:
            piece.append(f"+{sup} 条支持证据")
        if con:
            piece.append(f"+{con} 条反面证据")
        bits.append("、".join(piece))
    if d.removed_risks:
        bits.append(f"移除风险：{'、'.join(d.removed_risks)}")
    if d.added_risks:
        bits.append(f"新增风险：{'、'.join(d.added_risks)}")
    return f"{d.ticker}：论点{arrow}（" + "；".join(bits) + "）。"
