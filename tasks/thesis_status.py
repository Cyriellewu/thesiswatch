"""Thesis status engine — the brain of the "Today" screen.

Maps each holding to one of three honest states, per the locked product spec:
- no_action  : price noise, thesis intact
- watch      : new evidence arrived, but no re-judgement yet
- re_evaluate: a core assumption/risk/valuation changed, or an invalidation
               condition fired — the user should re-check the thesis

Pure and offline. Consumes an InvestmentThesis, its ThesisDelta, and the day's
signals map; returns a structured status (never a bare buy/sell).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from tasks.thesis import InvestmentThesis
from tasks.thesis_delta import ThesisDelta

Status = Literal["no_action", "watch", "re_evaluate"]

_STATUS_ZH = {
    "no_action": "无需行动",
    "watch": "留意",
    "re_evaluate": "重新评估",
}
_STATUS_EMOJI = {"no_action": "🟢", "watch": "🟡", "re_evaluate": "🟠"}


@dataclass
class ThesisStatus:
    ticker: str
    status: Status
    status_zh: str
    emoji: str
    headline_zh: str                        # one-line "why this status"
    delta: ThesisDelta
    confidence: dict[str, Any]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def classify(
    thesis: InvestmentThesis | None,
    delta: ThesisDelta,
    *,
    now: Any | None = None,
) -> ThesisStatus:
    """Decide the state for one holding. Priority: invalidation > weakened/
    strengthened material change > new evidence (watch) > no_action."""
    tk = delta.ticker
    conf = thesis.confidence(now=now) if thesis else {"score": delta.confidence_after, "level": "unknown", "coverage": 0.0, "freshness_days": None}
    reasons: list[str] = []

    if delta.invalidation_triggered:
        reasons.append("触发失效条件：" + "、".join(delta.invalidation_triggered))
        status: Status = "re_evaluate"
        headline = f"{tk}：核心假设可能被破坏，建议重新检查逻辑。"
    elif delta.direction in ("strengthened", "weakened") and abs(delta.confidence_change) >= 6:
        status = "re_evaluate"
        verb = "增强" if delta.direction == "strengthened" else "减弱"
        reasons.append(f"论点{verb}，信心 {delta.confidence_before:.0f}→{delta.confidence_after:.0f}")
        if delta.added_risks:
            reasons.append("新增风险：" + "、".join(delta.added_risks))
        headline = f"{tk}：论点{verb}明显，值得重新评估仓位。"
    elif delta.new_evidence or delta.added_risks or delta.direction in ("strengthened", "weakened"):
        status = "watch"
        if delta.new_evidence:
            reasons.append(f"新增 {len(delta.new_evidence)} 条证据")
        if delta.added_risks:
            reasons.append("新增风险：" + "、".join(delta.added_risks))
        headline = f"{tk}：有新变化但暂不需行动，先留意。"
    else:
        status = "no_action"
        reasons.append("仅价格波动，核心逻辑未变")
        headline = f"{tk}：无 thesis 级变化。"

    # Low-coverage theses get a gentle nudge to complete them (still no_action).
    if status == "no_action" and thesis is not None and conf.get("coverage", 0) < 0.4:
        reasons.append("论点尚不完整，建议补充风险/失效条件")

    return ThesisStatus(
        ticker=tk, status=status, status_zh=_STATUS_ZH[status], emoji=_STATUS_EMOJI[status],
        headline_zh=headline, delta=delta, confidence=conf, reasons=reasons,
    )


@dataclass
class PortfolioStatus:
    overall: Status
    overall_zh: str
    emoji: str
    headline_zh: str                        # "No action needed today" style
    needs_attention: list[ThesisStatus] = field(default_factory=list)   # re_evaluate
    worth_watching: list[ThesisStatus] = field(default_factory=list)    # watch
    no_change: list[ThesisStatus] = field(default_factory=list)         # no_action


def summarize_portfolio(statuses: list[ThesisStatus]) -> PortfolioStatus:
    """Roll per-holding statuses into the Today-screen summary. Honest No-action:
    if nothing is at re_evaluate/watch, say so plainly."""
    needs = [s for s in statuses if s.status == "re_evaluate"]
    watch = [s for s in statuses if s.status == "watch"]
    calm = [s for s in statuses if s.status == "no_action"]

    if needs:
        overall: Status = "re_evaluate"
        head = f"有 {len(needs)} 只持仓的逻辑发生实质变化，建议重新评估。"
    elif watch:
        overall = "watch"
        head = f"没有需要立刻行动的事；{len(watch)} 只有新变化，留意即可。"
    else:
        overall = "no_action"
        head = "今天无需行动：只是价格波动，核心逻辑没有变化。"

    return PortfolioStatus(
        overall=overall, overall_zh=_STATUS_ZH[overall], emoji=_STATUS_EMOJI[overall],
        headline_zh=head, needs_attention=needs, worth_watching=watch, no_change=calm,
    )
