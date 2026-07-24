"""Agent decision engine with strict schema and risk guardrails."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.decision_schema import Action, AgentRoundDecision, TradeDecision
from agents.llm_client import call_with_fallback
from agents.context import AgentContextBundle, account_row, position_snapshot

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

STYLE_CONFIG = {
    "conservative": {
        "name": "保守",
        "max_position_pct": 0.20,
        "max_speculative_pct": 0.05,
        "min_cash_pct": 0.20,
        "stop_loss_pct": -0.10,
        "max_holding_days_target": 60,
        "min_confidence_to_act": 0.70,
        "description": "本金优先，重视回撤。",
    },
    "balanced": {
        "name": "平衡",
        "max_position_pct": 0.15,
        "max_speculative_pct": 0.25,
        "min_cash_pct": 0.10,
        "stop_loss_pct": -0.08,
        "max_holding_days_target": 21,
        "min_confidence_to_act": 0.55,
        "description": "平衡收益和回撤。",
    },
    "aggressive": {
        "name": "激进",
        "max_position_pct": 0.10,
        "max_speculative_pct": 0.40,
        "min_cash_pct": 0.05,
        "stop_loss_pct": -0.06,
        "max_holding_days_target": 10,
        "min_confidence_to_act": 0.50,
        "description": "容忍波动，追求更高弹性。",
    },
}

SYSTEM_PROMPT_TEMPLATE = """你是 {style_name} 风格的虚拟投资 agent。
风格说明：{description}
硬性规则：
- 单票上限 {max_position_pct}%
- 现金下限 {min_cash_pct}%
- 信心阈值 {min_confidence_to_act}
- 允许动作 buy/add/hold/trim/sell/wait
- 输出必须是 JSON（不要 markdown）
"""


@dataclass
class AgentContext:
    agent_id: str
    style: str
    style_cfg: dict
    current_holdings: list[dict]
    cash: float
    total_equity: float
    today_pnl_pct: float
    macro_state: str
    macro_summary: str
    today_storylines: list[dict]
    holding_news: list[dict]
    candidate_signals: list[dict]
    past_lessons: list[str]
    investment_goal: str = ""


def build_user_prompt(ctx: AgentContext) -> str:
    holdings = "\n".join(
        f"- {h['ticker']}: {h['shares']:.4f} 股 @ ${h['current_price']:.2f} (成本 ${h.get('cost_basis',0):.2f})"
        for h in ctx.current_holdings
    ) or "（无持仓，全现金）"
    stories = "\n".join(
        f"- {s.get('title','?')}: {str(s.get('narrative',''))[:100]}" for s in ctx.today_storylines[:3]
    ) or "（暂无主线）"
    news = "\n".join(
        f"- [{n.get('severity','?')}] {n.get('primary_ticker','?')}: {n.get('one_line_zh') or n.get('title','')[:80]}"
        for n in ctx.holding_news[:10]
    ) or "（无重大新闻）"
    cands = "\n".join(
        f"- {c.get('ticker','?')}: score {c.get('score','?')} signals={','.join(c.get('signal_types', [])[:3])}"
        for c in ctx.candidate_signals[:8]
    ) or "（暂无高分候选）"
    lessons = "\n".join(f"- {x}" for x in ctx.past_lessons[:5]) or "（暂无教训）"
    return f"""账户状态：总权益 ${ctx.total_equity:,.2f}, 现金 ${ctx.cash:,.2f}, 当日P/L {ctx.today_pnl_pct:+.2f}%
目标：{ctx.investment_goal or '平衡收益与回撤'}
持仓：
{holdings}
宏观：{ctx.macro_state} / {ctx.macro_summary}
今日主线：
{stories}
持仓新闻：
{news}
雷达候选：
{cands}
历史教训：
{lessons}
请输出 AgentRoundDecision JSON。"""


def enforce_risk_rules(decision: AgentRoundDecision, ctx: AgentContext) -> AgentRoundDecision:
    cfg = ctx.style_cfg
    fixed: list[TradeDecision] = []
    warnings = 0
    for d in decision.decisions:
        cur = d
        if cur.action in {Action.BUY, Action.ADD, Action.SELL, Action.TRIM} and cur.confidence < cfg["min_confidence_to_act"]:
            cur = cur.model_copy(
                update={
                    "action": Action.HOLD,
                    "size_pct": 0.0,
                    "reasoning": f"[风控] 信心不足改 HOLD。原理由: {cur.reasoning}",
                }
            )
            warnings += 1
        if cur.action in {Action.BUY, Action.ADD} and cur.size_pct > cfg["max_position_pct"]:
            cur = cur.model_copy(update={"size_pct": cfg["max_position_pct"]})
            warnings += 1
        if ctx.macro_state == "risk_off" and cur.action == Action.BUY:
            cur = cur.model_copy(
                update={"action": Action.HOLD, "size_pct": 0.0, "reasoning": f"[风控] risk_off 改 HOLD。原理由: {cur.reasoning}"}
            )
            warnings += 1
        if cur.action in {Action.BUY, Action.ADD} and ctx.total_equity > 0:
            cash_after = (ctx.cash - cur.size_pct * ctx.total_equity) / ctx.total_equity
            if cash_after < cfg["min_cash_pct"]:
                cur = cur.model_copy(
                    update={"action": Action.HOLD, "size_pct": 0.0, "reasoning": f"[风控] 现金下限不足改 HOLD。原理由: {cur.reasoning}"}
                )
                warnings += 1
            elif not cur.stop_loss_price and cur.entry_price_max:
                cur = cur.model_copy(update={"stop_loss_price": cur.entry_price_max * (1 + cfg["stop_loss_pct"])})
        fixed.append(cur)
    summary = decision.summary + (f" [风控调整: {warnings}]" if warnings else "")
    return decision.model_copy(update={"summary": summary, "decisions": fixed})


def run_agent_decision(ctx: AgentContext) -> tuple[AgentRoundDecision | None, dict]:
    cfg = ctx.style_cfg
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        style_name=cfg["name"],
        description=cfg["description"],
        max_position_pct=int(cfg["max_position_pct"] * 100),
        min_cash_pct=int(cfg["min_cash_pct"] * 100),
        min_confidence_to_act=cfg["min_confidence_to_act"],
    )
    user_prompt = build_user_prompt(ctx)
    out = call_with_fallback(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AgentRoundDecision,
        primary=("gemini", "gemini-2.5-flash"),
        fallback=("groq", "llama-3.3-70b-versatile"),
        max_retries_per_provider=2,
    )
    meta = {
        "provider": out.provider,
        "model": out.model,
        "attempts": out.attempts,
        "raw_text_len": len(out.raw_text or ""),
        "error": out.error if not out.success else None,
    }
    if not out.success:
        return None, meta
    decision = out.data
    assert isinstance(decision, AgentRoundDecision)
    return enforce_risk_rules(decision, ctx), meta


def save_decision(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    decision: AgentRoundDecision | None,
    metadata: dict,
    ctx_snapshot: dict | None = None,
) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    success = 1 if decision is not None else 0
    summary = decision.summary if decision else ""
    macro_view = decision.macro_view if decision else "neutral"
    decisions_json = (
        json.dumps([d.model_dump(mode="json") for d in decision.decisions], ensure_ascii=False)
        if decision
        else "[]"
    )
    action = "HOLD" if decision is not None else "ERROR"
    symbol = None
    plain_reason = "" if decision is not None else str(metadata.get("error") or "AI 决策失败")
    plain_risk = ""
    next_watch = ""
    confidence = 0.0
    dollar_amount = 0.0
    if decision and decision.decisions:
        d0 = decision.decisions[0]
        action = d0.action.value.upper()
        symbol = d0.ticker
        plain_reason = d0.reasoning
        plain_risk = ""
        next_watch = f"{decision.next_check_in_hours}h"
        confidence = d0.confidence
        dollar_amount = d0.size_pct * 100.0
    cur = conn.execute(
        """
        INSERT INTO agent_decisions
        (account_id, ts, trigger, success, summary, macro_view, decisions_json, next_check_in_hours,
         provider, model, attempts, error, ctx_snapshot, input_digest_json, decision_json, used_llm,
         action, symbol, symbol_to, dollar_amount, plain_reason, plain_risk, next_watch, confidence, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            agent_id,
            ts,
            "runner",
            success,
            summary,
            macro_view,
            decisions_json,
            (decision.next_check_in_hours if decision else 24),
            metadata.get("provider") or "",
            metadata.get("model") or "",
            int(metadata.get("attempts") or 0),
            metadata.get("error") or "",
            json.dumps(ctx_snapshot or {}, ensure_ascii=False),
            json.dumps(ctx_snapshot or {}, ensure_ascii=False),
            decisions_json,
            1 if metadata.get("provider") else 0,
            action,
            symbol,
            None,
            dollar_amount,
            plain_reason,
            plain_risk,
            next_watch,
            confidence,
            "",
        ),
    )
    return int(cur.lastrowid)


def ny_today_iso() -> str:
    return datetime.now(NY).date().isoformat()


def meta_cycle_date_et(conn: sqlite3.Connection) -> str | None:
    try:
        r = conn.execute("SELECT v FROM meta_kv WHERE k = 'agent_cycle_date_et' LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return str(r["v"]) if r else None


def mark_cycle_et_date(conn: sqlite3.Connection, day_iso: str) -> None:
    conn.execute(
        """INSERT INTO meta_kv (k, v, updated_at) VALUES ('agent_cycle_date_et', ?, datetime('now'))
           ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = datetime('now')""",
        (day_iso,),
    )


def _parse_rules(raw: object, style: str | None) -> dict[str, float]:
    rules = {"min_cash_pct": 10.0, "max_single_pct": 25.0}
    if style == "conservative":
        rules = {"min_cash_pct": 20.0, "max_single_pct": 15.0}
    elif style == "aggressive":
        rules = {"min_cash_pct": 5.0, "max_single_pct": 35.0}
    try:
        extra = json.loads(str(raw or "{}"))
        if "min_cash_pct" in extra:
            rules["min_cash_pct"] = float(extra["min_cash_pct"])
        if "max_single_pct" in extra:
            rules["max_single_pct"] = float(extra["max_single_pct"])
    except Exception:
        pass
    return rules


def _allowed_buy_symbols(mode: str, holdings: list[str], opps: list[str]) -> set[str]:
    base = {"VOO", "QQQ"}
    base |= {str(x).upper() for x in holdings}
    base |= {str(x).upper() for x in opps}
    if mode != "fresh":
        base |= {"MSFT", "GOOGL", "AVGO", "META", "NVDA", "TSLA", "PANW"}
    return {x for x in base if x}


def _decision_to_norm(
    d: TradeDecision,
    *,
    equity: float,
    rules: dict[str, float],
    allowed: set[str],
    held: set[str],
    px_map: dict[str, float],
) -> dict[str, Any]:
    sym = d.ticker.upper()
    risk_bits: list[str] = []
    if d.stop_loss_price:
        risk_bits.append(f"止损参考 ${d.stop_loss_price:.2f}")
    if d.take_profit_price:
        risk_bits.append(f"止盈参考 ${d.take_profit_price:.2f}")
    risk = "；".join(risk_bits) or "执行引擎继续卡现金下限和单票上限。"
    next_watch = f"{d.expected_holding_days} 天内复查；下一轮继续看新闻/价格。"

    if d.action in {Action.HOLD, Action.WAIT}:
        return {
            "action": "HOLD",
            "symbol": sym if sym in held else None,
            "symbol_to": None,
            "dollar_amount": 0.0,
            "plain_reason": d.reasoning,
            "plain_risk": risk,
            "next_watch": next_watch,
            "confidence": d.confidence,
        }

    if d.action in {Action.BUY, Action.ADD}:
        if sym not in allowed:
            return {
                "action": "HOLD",
                "symbol": None,
                "symbol_to": None,
                "dollar_amount": 0.0,
                "plain_reason": f"{sym} 不在允许买入名单，拒绝执行。原理由：{d.reasoning}",
                "plain_risk": risk,
                "next_watch": next_watch,
                "confidence": d.confidence,
            }
        px = float(px_map.get(sym) or 0.0)
        if d.entry_price_max and px and px > float(d.entry_price_max):
            return {
                "action": "HOLD",
                "symbol": None,
                "symbol_to": None,
                "dollar_amount": 0.0,
                "plain_reason": f"{sym} 当前 ${px:.2f} 高于限价 ${float(d.entry_price_max):.2f}，不追。",
                "plain_risk": risk,
                "next_watch": next_watch,
                "confidence": d.confidence,
            }
        max_pct = float(rules.get("max_single_pct") or 25.0) / 100.0
        amount = min(float(d.size_pct or 0.0), max_pct) * max(0.0, equity)
        return {
            "action": "BUY",
            "symbol": sym,
            "symbol_to": None,
            "dollar_amount": amount,
            "plain_reason": d.reasoning,
            "plain_risk": risk,
            "next_watch": next_watch,
            "confidence": d.confidence,
        }

    if d.action == Action.SELL:
        return {
            "action": "SELL" if sym in held else "HOLD",
            "symbol": sym if sym in held else None,
            "symbol_to": None,
            "dollar_amount": 0.0,
            "plain_reason": d.reasoning if sym in held else f"账户未持有 {sym}，不能卖。原理由：{d.reasoning}",
            "plain_risk": risk,
            "next_watch": next_watch,
            "confidence": d.confidence,
        }

    if d.action == Action.TRIM:
        amount = float(d.size_pct or 0.0) * max(0.0, equity)
        return {
            "action": "TRIM" if sym in held else "HOLD",
            "symbol": sym if sym in held else None,
            "symbol_to": None,
            "dollar_amount": amount,
            "plain_reason": d.reasoning if sym in held else f"账户未持有 {sym}，不能减仓。原理由：{d.reasoning}",
            "plain_risk": risk,
            "next_watch": next_watch,
            "confidence": d.confidence,
        }

    return {
        "action": "HOLD",
        "symbol": None,
        "symbol_to": None,
        "dollar_amount": 0.0,
        "plain_reason": f"{sym} 动作无法映射，暂不执行。",
        "plain_risk": risk,
        "next_watch": next_watch,
        "confidence": d.confidence,
    }


def _round_to_norms(
    decision: AgentRoundDecision,
    *,
    equity: float,
    rules: dict[str, float],
    allowed: set[str],
    held: set[str],
    px_map: dict[str, float],
) -> list[dict[str, Any]]:
    norms = [
        _decision_to_norm(d, equity=equity, rules=rules, allowed=allowed, held=held, px_map=px_map)
        for d in decision.decisions
    ]
    actionable = [n for n in norms if n.get("action") != "HOLD" and float(n.get("dollar_amount") or 0.0) >= 0.0]
    if actionable:
        return actionable[:3]
    if norms:
        return [norms[0]]
    return [
        {
            "action": "HOLD",
            "symbol": None,
            "symbol_to": None,
            "dollar_amount": 0.0,
            "plain_reason": decision.summary,
            "plain_risk": "模型没有给出具体可执行动作。",
            "next_watch": f"{decision.next_check_in_hours}h",
            "confidence": 0.5,
        }
    ]


def _cash_and_equity(conn: sqlite3.Connection, account_id: str, px_map: dict[str, float]) -> tuple[float, float, float]:
    row = conn.execute("SELECT cash_usd FROM agent_accounts WHERE id = ?", (account_id,)).fetchone()
    cash = float(row["cash_usd"] if row else 0.0)
    mv = 0.0
    for p in position_snapshot(conn, account_id):
        mv += float(p["qty"]) * float(px_map.get(str(p["symbol"]).upper()) or 0.0)
    return cash + mv, cash, mv


def _record_token_usage(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    prompt: str,
    raw_text_len: int,
    metadata: dict,
    success: bool,
    reason: str,
) -> None:
    try:
        conn.execute(
            """INSERT INTO token_usage
               (feature, agent_id, model, input_tokens, output_tokens, success, fallback_used, reason_for_call)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "agent_decision",
                account_id,
                metadata.get("model") or "",
                max(1, len(prompt) // 4),
                max(1, int(raw_text_len or 0) // 4),
                1 if success else 0,
                1 if metadata.get("provider") == "groq" else 0,
                reason[:900],
            ),
        )
    except sqlite3.OperationalError:
        pass


def decide_for_account(
    conn: sqlite3.Connection,
    bundle: AgentContextBundle,
    px_map: dict[str, float],
    *,
    account_id: str,
    first_daily: bool,
    urgent_market: bool,
) -> dict[str, Any]:
    row = account_row(conn, account_id)
    if not row or int(row["allow_trades"] or 0) != 1:
        return {"skipped": True}

    mode = str(row["mode"])
    style = str(row["style"] or "balanced")
    rules = _parse_rules(row["rules_json"], style)
    positions = position_snapshot(conn, account_id)
    held = {str(p["symbol"]).upper() for p in positions}
    equity, cash, mv = _cash_and_equity(conn, account_id, px_map)
    allowed = _allowed_buy_symbols(mode, bundle.holdings, bundle.opportunity_syms)
    triggered = urgent_market or first_daily or any(float(s.get("score") or 0) >= 60 for s in bundle.signals)

    if not triggered:
        return {"skipped": True}

    holdings_ctx = [
        {
            "ticker": p["symbol"],
            "shares": float(p["qty"]),
            "cost_basis": float(p["avg"]),
            "current_price": float(px_map.get(str(p["symbol"]).upper()) or 0.0),
        }
        for p in positions
    ]
    candidates = [
        {
            "ticker": o.get("ticker"),
            "score": o.get("score"),
            "signal_types": [o.get("bucket")] if o.get("bucket") else [],
        }
        for o in bundle.opportunities[:10]
    ]
    holding_news = [
        {"primary_ticker": "", "severity": "attention", "one_line_zh": line}
        for line in bundle.news_lines[:10]
    ]
    ctx = AgentContext(
        agent_id=account_id,
        style=style,
        style_cfg=STYLE_CONFIG.get(style, STYLE_CONFIG["balanced"]),
        current_holdings=holdings_ctx,
        cash=cash,
        total_equity=equity or (cash + mv) or 1.0,
        today_pnl_pct=0.0,
        macro_state="risk_off" if bundle.macro_risk_high else "neutral",
        macro_summary=bundle.macro_text or "暂无宏观快照。",
        today_storylines=[],
        holding_news=holding_news,
        candidate_signals=candidates,
        past_lessons=[],
        investment_goal=str(row["agent_goal_json"] or ""),
    )

    prompt_preview = build_user_prompt(ctx)
    decision, metadata = run_agent_decision(ctx)
    ctx_snapshot = {
        "cash": cash,
        "equity": equity,
        "positions": len(positions),
        "signals": len(bundle.signals),
        "opportunities": len(bundle.opportunities),
        "macro_risk_high": bundle.macro_risk_high,
        "allowed_buy": sorted(allowed)[:80],
    }
    did = save_decision(conn, agent_id=account_id, decision=decision, metadata=metadata, ctx_snapshot=ctx_snapshot)
    _record_token_usage(
        conn,
        account_id=account_id,
        prompt=prompt_preview,
        raw_text_len=int(metadata.get("raw_text_len") or 0),
        metadata=metadata,
        success=decision is not None,
        reason="daily_or_trigger",
    )

    if decision is None:
        return {
            "account_id": account_id,
            "decision_id": did,
            "failed": True,
            "norm": {"action": "ERROR", "plain_reason": metadata.get("error") or "AI 决策失败"},
            "rules": rules,
            "style": style,
            "mode": mode,
        }

    norms = _round_to_norms(decision, equity=equity, rules=rules, allowed=allowed, held=held, px_map=px_map)
    return {
        "account_id": account_id,
        "decision_id": did,
        "norm": norms[0],
        "norms": norms,
        "used_llm": True,
        "rules": rules,
        "style": style,
        "mode": mode,
    }
