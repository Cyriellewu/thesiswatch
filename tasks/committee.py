"""投资委员会 (Investment Committee) — multi-persona debate over facts.

Signature agentic feature: 3-4 persona agents (价值派/动量派/风控官/大佬跟随) each
give a short, fact-bound view; a 主持人 (Chair) synthesizes a final call.

Honesty contract (non-negotiable):
- The LLM is a *renderer of pre-computed facts*, never the *source*. The pure
  rule engine below always produces every persona's facts, scores, veto, and a
  fully-formed Chinese rationale — the feature reads well with ZERO LLM.
- If a key is configured, an optional LLM only *rephrases* the given bullets; a
  post-hoc numeric fact-guard discards any prose that introduces new numbers.
- Risk-manager veto and ⚠️ warnings live on the dataclass, computed in the pure
  engine — an LLM cannot remove them.

The pure functions (`build_committee_facts`, `synthesize_rule_based`) take no I/O
and are exhaustively unit-tested offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Persona = Literal["value", "momentum", "risk", "smartmoney"]
Stance = Literal["bullish", "neutral", "bearish", "veto"]

_VETO_WARNING = "⚠️ 破坏你的集中度规则"


@dataclass
class PersonaView:
    persona: Persona
    name_zh: str
    stance: Stance
    score: float
    bullets: list[str]
    facts: dict[str, Any] = field(default_factory=dict)
    veto: bool = False
    prose_zh: str = ""
    source: Literal["rule", "llm"] = "rule"


@dataclass
class Verdict:
    subject: str
    scope: Literal["stock", "portfolio"]
    call_zh: str
    stance: Stance
    confidence: int
    rationale_zh: str
    dissent_zh: str
    warnings: list[str]
    views: list[PersonaView]
    source: Literal["rule", "llm"]
    as_of: str


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _band(score: float) -> Stance:
    if score >= 60:
        return "bullish"
    if score <= 40:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Pure persona reducers
# ---------------------------------------------------------------------------


def _value_view(stock: dict[str, Any] | None) -> PersonaView:
    bullets: list[str] = []
    score = 50.0
    facts: dict[str, Any] = {}
    s = stock or {}
    pos52 = s.get("pos_52")
    pnl = s.get("pnl_pct")
    action = s.get("action", "Hold")
    facts.update({"pos_52": pos52, "pnl_pct": pnl, "action": action})

    if isinstance(pos52, (int, float)):
        if pos52 <= 25:
            score += 12
            bullets.append(f"接近年内低位（52周位置 {pos52:.0f}%），估值相对占优。")
        elif pos52 >= 90:
            score -= 12
            bullets.append(f"接近年内高位（52周位置 {pos52:.0f}%），安全边际薄。")
    else:
        bullets.append("52周位置数据暂缺。")

    if isinstance(pnl, (int, float)) and pnl < -15:
        score += 6
        bullets.append(f"已明显回撤（{pnl:+.0f}%），若基本面未变则性价比上升。")

    if action in ("Add on weakness", "Buy small"):
        score += 5
        bullets.append("规则动作也偏逢低布局，方向一致。")
    elif action in ("Do not chase", "Avoid for now"):
        score -= 5
        bullets.append("规则动作偏谨慎，不宜追高。")

    bullets.append("（注：本地无基本面数据，此为价格位置代理判断）")
    score = _clamp(score, 0, 100)
    return PersonaView("value", "价值派", _band(score), round(score, 1), bullets, facts)


def _momentum_view(
    stock: dict[str, Any] | None, focus_entry: dict[str, Any] | None
) -> PersonaView:
    s = stock or {}
    fe = focus_entry or {}
    score = float(fe.get("conviction", 50.0))
    direction = fe.get("direction", "中性观望")
    bullets: list[str] = []
    for fac in sorted(fe.get("factors", []), key=lambda f: abs(f.get("delta", 0)), reverse=True)[:3]:
        arrow = "＋" if fac.get("delta", 0) >= 0 else "－"
        bullets.append(f"{fac.get('label','')} {arrow}{abs(fac.get('delta',0)):.0f}（{fac.get('detail','')}）")
    chg = s.get("chg_pct")
    rsi = s.get("rsi")
    if isinstance(chg, (int, float)):
        bullets.append(f"今日 {chg:+.1f}%。")
    if isinstance(rsi, (int, float)):
        bullets.append(f"RSI {rsi:.0f}。")
    if not bullets:
        bullets.append("动量数据暂缺。")
    stance: Stance = (
        "bullish" if direction in ("重点关注", "偏多留意")
        else "bearish" if direction == "谨慎回避"
        else "neutral"
    )
    facts = {"conviction": score, "direction": direction, "chg_pct": chg, "rsi": rsi}
    return PersonaView("momentum", "动量派", stance, round(score, 1), bullets, facts)


def _risk_view(
    stock: dict[str, Any] | None,
    concentration_flags: list[str],
    portfolio: dict[str, Any] | None,
    scope: str,
) -> PersonaView:
    s = stock or {}
    bullets: list[str] = []
    veto = False
    score = 50.0
    facts: dict[str, Any] = {"concentration_flags": list(concentration_flags or [])}
    ticker = str(s.get("ticker", "")).upper()
    weight = s.get("weight_pct")

    single_flag = next((f for f in (concentration_flags or []) if f.startswith("单票偏重")), None)
    tech_flag = next((f for f in (concentration_flags or []) if "科技偏重" in f), None)

    if scope == "stock" and single_flag and ticker and ticker in single_flag:
        veto = True
        score = 25.0
        bullets.append(f"该票权重{f' {weight:.0f}%' if isinstance(weight,(int,float)) else ''}超过你设定的单票上限。")
    elif isinstance(weight, (int, float)) and weight >= 25:
        score = 35.0
        bullets.append(f"该票权重已达 {weight:.0f}%，接近集中度红线。")

    if tech_flag:
        score = min(score, 40.0)
        tw = (portfolio or {}).get("tech_weight_pct")
        bullets.append(f"组合科技敞口已{f' {tw:.0f}%' if isinstance(tw,(int,float)) else '偏高'}，再加剧集中。")

    chg = s.get("chg_pct")
    if isinstance(chg, (int, float)) and abs(chg) >= 6:
        score = min(score, 42.0)
        bullets.append(f"今日波动大（{chg:+.1f}%），注意情绪化交易。")

    if scope == "portfolio":
        rl = (portfolio or {}).get("risk_level", "routine")
        flags = concentration_flags or []
        if flags:
            score = min(score, 40.0)
            bullets.append("集中度提醒：" + "、".join(flags))
        bullets.append(f"当前风险档：{rl}。")

    if not bullets:
        bullets.append("集中度与波动正常，无特别风险提示。")

    stance: Stance = "veto" if veto else ("bearish" if score <= 40 else "neutral")
    return PersonaView("risk", "风控官", stance, round(_clamp(score, 0, 100), 1), bullets, facts, veto=veto)


def _smartmoney_view(guru_overlap: list[dict[str, Any]] | None) -> PersonaView:
    bullets: list[str] = []
    score = 50.0
    facts: dict[str, Any] = {}
    if guru_overlap is None:
        bullets.append("大佬数据未加载（离线/未抓取），本席暂持中性。")
        return PersonaView("smartmoney", "大佬跟随", "neutral", 50.0, bullets, facts)
    facts["overlap_count"] = len(guru_overlap)
    if guru_overlap:
        total = sum(float(h.get("pct") or 0.0) for h in guru_overlap)
        top = max(guru_overlap, key=lambda h: float(h.get("pct") or 0.0))
        boost = _clamp(len(guru_overlap) * 3.0 + total * 0.8, 0, 30)
        score += boost
        who = str(top.get("label", "")).split(" (")[0]
        bullets.append(f"{len(guru_overlap)} 位大佬也持有（如 {who} 占其组合 {float(top.get('pct') or 0):.1f}%）。")
        facts["total_pct"] = round(total, 1)
    else:
        bullets.append("无大佬持有记录；注意 13F 季度延迟、不含 ETF/空头，看不到很正常，别据此看空。")
    # Absence is never bearish.
    stance: Stance = "bullish" if score >= 60 else "neutral"
    return PersonaView("smartmoney", "大佬跟随", stance, round(_clamp(score, 0, 100), 1), bullets, facts)


def build_committee_facts(
    stock: dict[str, Any] | None,
    focus_entry: dict[str, Any] | None,
    concentration_flags: list[str],
    guru_overlap: list[dict[str, Any]] | None,
    in_news: bool,
    scope: Literal["stock", "portfolio"] = "stock",
    portfolio: dict[str, Any] | None = None,
) -> list[PersonaView]:
    """PURE. Build the four persona views from pre-computed facts. Never raises."""
    # Momentum needs a conviction entry; recompute on the fly if not in focus.
    if focus_entry is None and stock is not None:
        try:
            from tasks import conviction  # noqa: PLC0415

            guru_pct = sum(float(h.get("pct") or 0.0) for h in (guru_overlap or []))
            focus_entry = conviction.score_stock(
                stock, guru_pct=guru_pct, guru_count=len(guru_overlap or []), in_news=in_news
            )
        except Exception:
            focus_entry = None
    views = [
        _value_view(stock),
        _momentum_view(stock, focus_entry),
        _risk_view(stock, concentration_flags, portfolio, scope),
        _smartmoney_view(guru_overlap),
    ]
    for v in views:
        v.prose_zh = "；".join(v.bullets)
    return views


_PERSONA_WEIGHT = {"momentum": 1.0, "value": 0.9, "smartmoney": 0.7, "risk": 1.1}


def synthesize_rule_based(
    subject: str,
    scope: Literal["stock", "portfolio"],
    views: list[PersonaView],
    as_of: str,
) -> Verdict:
    """PURE. Deterministic chair: veto gate → weighted vote → confidence."""
    warnings: list[str] = []
    vetoed = any(v.veto for v in views)
    for v in views:
        if v.veto:
            warnings.append(_VETO_WARNING)

    scores = [v.score for v in views]
    spread = (max(scores) - min(scores)) if scores else 0.0

    # Weighted signed vote around 50.
    num = sum((v.score - 50.0) * _PERSONA_WEIGHT.get(v.persona, 1.0) for v in views)
    den = sum(_PERSONA_WEIGHT.get(v.persona, 1.0) for v in views) or 1.0
    net = 50.0 + num / den

    if vetoed:
        stance: Stance = "veto"
        confidence = int(_clamp(35 - spread * 0.2, 5, 35))
        call = f"先控风险：{subject} 触发集中度规则，暂缓加仓、优先降低单票/板块暴露。"
    else:
        stance = _band(net)
        confidence = int(_clamp(100 - spread, 5, 95))
        lean = {"bullish": "偏多", "neutral": "中性", "bearish": "偏空"}[stance]
        if scope == "portfolio":
            call = f"组合整体{lean}：按纪律执行，注意下方风控提示。"
        else:
            verb = {"bullish": "可小额分批、控制加仓幅度", "neutral": "持有观察为主", "bearish": "不追高、等确认"}[stance]
            call = f"{subject}：委员会{lean}，{verb}。"

    # Cite who said what.
    parts = []
    for v in views:
        tag = {"bullish": "看多", "neutral": "中性", "bearish": "偏空", "veto": "否决"}[v.stance]
        head = v.bullets[0] if v.bullets else ""
        parts.append(f"{v.name_zh}{tag}（{v.score:.0f}分）：{head}")
    rationale = "；".join(parts)

    # Dissent: strongest view opposing the net stance.
    if vetoed:
        dissent = "风控官行使否决权，其余观点被压制，先降风险。"
    else:
        opposing = [v for v in views if v.stance not in (stance, "neutral")]
        if opposing:
            d = max(opposing, key=lambda v: abs(v.score - 50))
            dissent = f"分歧：{d.name_zh}持{ {'bullish':'看多','bearish':'偏空','veto':'否决'}[d.stance] }意见（{d.score:.0f}分）。"
        else:
            dissent = "委员会基本一致。"

    return Verdict(
        subject=subject, scope=scope, call_zh=call, stance=stance,
        confidence=confidence, rationale_zh=rationale, dissent_zh=dissent,
        warnings=list(dict.fromkeys(warnings)), views=views, source="rule", as_of=as_of,
    )


# ---------------------------------------------------------------------------
# Optional LLM polish (degrades to identity; fact-guarded)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return set(_NUM_RE.findall(text or ""))


def factguard_ok(source_texts: list[str], candidate: str) -> bool:
    """True iff `candidate` introduces no numeric token absent from the sources."""
    allowed: set[str] = set()
    for t in source_texts:
        allowed |= _numbers(t)
    return _numbers(candidate).issubset(allowed)


class CommitteeLLM:
    """Wraps TokenGuard. Rewrites rule bullets → prose. Never adds facts."""

    def __init__(self, guard: Any = None, chair_guard: Any = None) -> None:
        self._guard = guard
        self._chair_guard = chair_guard

    def _call(self, guard: Any, task: str, prompt: str) -> str:
        try:
            res = guard.call(task, prompt)
            return (res or {}).get("text", "") if isinstance(res, dict) else str(res or "")
        except Exception:
            return ""

    def polish_persona(self, view: PersonaView) -> PersonaView:
        if self._guard is None:
            return view
        prompt = (
            "你是投资委员会中的一位分析师，把下面的要点改写成 1-2 句流畅中文，"
            "不得新增任何数字、新闻或事实，只能重述给定要点：\n" + "\n".join(view.bullets)
        )
        text = self._call(self._guard, f"committee:persona:{view.persona}", prompt).strip()
        if text and factguard_ok(view.bullets, text):
            view.prose_zh = text
            view.source = "llm"
        return view

    def synthesize_chair(self, base: Verdict) -> Verdict:
        if self._chair_guard is None:
            return base
        sources = [base.call_zh, base.rationale_zh, base.dissent_zh]
        prompt = (
            "你是投资委员会主持人，把下面的结论与各方观点综合成 2-3 句自然中文，"
            "保留结论方向与信心，不得新增数字或新闻：\n"
            f"结论：{base.call_zh}\n依据：{base.rationale_zh}\n分歧：{base.dissent_zh}"
        )
        text = self._call(self._chair_guard, "committee:chair", prompt).strip()
        if text and factguard_ok(sources, text):
            base.rationale_zh = text
            base.source = "llm"
        return base


def make_default_llm() -> CommitteeLLM | None:
    """Return a CommitteeLLM iff a key is configured, else None (offline)."""
    try:
        import os  # noqa: PLC0415

        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")):
            return None
        from llm.router import Limits, TokenGuard  # noqa: PLC0415

        return CommitteeLLM(guard=TokenGuard(), chair_guard=TokenGuard(Limits(flash_lite_cap=0)))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry points (glue: facts → rule verdict → optional LLM upgrade)
# ---------------------------------------------------------------------------


def _overlap_for(ticker: str, gurus: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if gurus is None:
        return None
    out: list[dict[str, Any]] = []
    tk = ticker.upper()
    for g in gurus:
        for h in g.get("holdings") or []:
            if str(h.get("symbol", "")).upper() == tk:
                out.append({"label": g.get("label", ""), "pct": float(h.get("pct") or 0.0)})
    return out


def run_committee_for_stock(
    advice: dict[str, Any],
    ticker: str,
    *,
    gurus: list[dict[str, Any]] | None = None,
    use_llm: bool = True,
    llm: CommitteeLLM | None = None,
) -> Verdict:
    tk = str(ticker or "").upper()
    stock = next((s for s in advice.get("stocks", []) if str(s.get("ticker", "")).upper() == tk), None)
    focus_entry = next((f for f in advice.get("focus", []) if str(f.get("ticker", "")).upper() == tk), None)
    flags = (advice.get("portfolio", {}) or {}).get("concentration_flags", []) or []
    news_tk = {str(i.get("ticker", "")).upper() for i in (advice.get("news", {}) or {}).get("holdings", [])}
    overlap = _overlap_for(tk, gurus)

    views = build_committee_facts(stock, focus_entry, flags, overlap, tk in news_tk,
                                  scope="stock", portfolio=advice.get("portfolio"))
    verdict = synthesize_rule_based(tk, "stock", views, advice.get("as_of_label", ""))
    return _maybe_polish(verdict, use_llm, llm)


def run_committee_for_portfolio(
    advice: dict[str, Any],
    *,
    gurus: list[dict[str, Any]] | None = None,
    use_llm: bool = True,
    llm: CommitteeLLM | None = None,
) -> Verdict:
    flags = (advice.get("portfolio", {}) or {}).get("concentration_flags", []) or []
    # Aggregate momentum from the focus leaders for a portfolio-level read.
    focus = advice.get("focus", []) or []
    avg_conv = sum(f.get("conviction", 50) for f in focus) / len(focus) if focus else 50.0
    pseudo_focus = {"conviction": avg_conv, "direction": "中性观望", "factors": []}
    views = build_committee_facts(None, pseudo_focus, flags, None, False,
                                  scope="portfolio", portfolio=advice.get("portfolio"))
    verdict = synthesize_rule_based("PORTFOLIO", "portfolio", views, advice.get("as_of_label", ""))
    return _maybe_polish(verdict, use_llm, llm)


def _maybe_polish(verdict: Verdict, use_llm: bool, llm: CommitteeLLM | None) -> Verdict:
    if not use_llm:
        return verdict
    engine = llm or make_default_llm()
    if engine is None:
        return verdict
    verdict.views = [engine.polish_persona(v) for v in verdict.views]
    return engine.synthesize_chair(verdict)
