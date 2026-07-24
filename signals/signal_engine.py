"""Rule-based trade discipline signals (Murphy + Minervini + Douglas + Elder, simplified)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from signals.indicators import bars_summary, load_daily_bars
from signals.market_regime import compute_market_regime
from signals.risk import planned_stop, reward_risk, suggest_shares
from signals.setups import pivot_level, setup_metrics
from signals.trend_template import trend_flags

ROOT = Path(__file__).resolve().parents[1]

ACTION_ZH: dict[str, str] = {
    "AVOID": "暂不考虑",
    "WATCH": "可关注 / 观察",
    "SETUP_FORMING": "形态形成中",
    "NEAR_ENTRY": "接近买点",
    "BUY_TRIGGERED": "突破确认（计划内）",
    "HOLD": "继续持有",
    "REDUCE_OR_EXIT": "减仓/退出考虑",
    "STOP_TRIGGERED": "跌破止损计划",
    "EXTENDED_DONT_CHASE": "已延伸，别追",
}


def _load_engine_cfg() -> dict[str, Any]:
    p = ROOT / "config" / "settings.yaml"
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    block = raw.get("signal_engine") or {}
    return {
        "near_pivot_pct": float(block.get("near_pivot_pct", 0.03)),
        "extended_pct": float(block.get("max_extended_pct", 0.08)),
        "volume_confirm_ratio": float(block.get("volume_confirm_ratio", 1.5)),
        "min_reward_risk": float(block.get("min_reward_risk", 2.0)),
        "risk_per_trade_pct": float(block.get("risk_per_trade_pct", 0.01)),
        "max_loss_pct": float(block.get("max_loss_pct", 0.07)),
        "target_pivot_mult": float(block.get("target_pivot_mult", 1.08)),
    }


def get_signal_engine_config() -> dict[str, Any]:
    """供 UI / 书籍卡片读取 engine 阈值（与 `_load_engine_cfg` 相同）。"""
    return _load_engine_cfg()


@dataclass
class TradeDisciplineSignal:
    ticker: str
    action: str
    action_zh: str
    confidence: int
    reasons: list[str] = field(default_factory=list)
    entry: float | None = None
    stop: float | None = None
    risk_pct: float | None = None
    reward_risk: float | None = None
    pivot: float | None = None
    status_zh: str = ""
    market_regime: str = ""
    market_regime_zh: str = ""
    trend_score: int = 0
    rs_vs_qqq_pct: float = 0.0
    fomo_hint: str = ""
    suggest_shares: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "action": self.action,
            "action_zh": self.action_zh,
            "confidence": self.confidence,
            "reason": self.reasons,
            "entry": self.entry,
            "stop": self.stop,
            "risk_pct": self.risk_pct,
            "reward_risk": self.reward_risk,
            "pivot": self.pivot,
            "status": self.status_zh,
            "market_regime": self.market_regime,
            "market_regime_zh": self.market_regime_zh,
            "trend_score": self.trend_score,
            "rs_vs_qqq_pct": self.rs_vs_qqq_pct,
            "fomo_hint": self.fomo_hint,
            "suggest_shares": self.suggest_shares,
        }


def _score_trend(trend_ok: bool, strong: bool) -> int:
    if strong:
        return 90
    if trend_ok:
        return 70
    return 35


def evaluate_trade_discipline(
    ticker: str,
    *,
    avg_cost: float | None = None,
    account_equity: float | None = None,
    summary_precalc: dict[str, Any] | None = None,
) -> TradeDisciplineSignal:
    """Produce a discipline label, not a price prediction."""
    cfg = _load_engine_cfg()
    sym = str(ticker or "").strip().upper()
    regime = compute_market_regime()
    reasons: list[str] = []

    if summary_precalc:
        summary = dict(summary_precalc)
    else:
        h = load_daily_bars(sym)
        summary = bars_summary(h) if h is not None and not h.empty else {}
    if not summary:
        return TradeDisciplineSignal(
            ticker=sym,
            action="AVOID",
            action_zh=ACTION_ZH["AVOID"],
            confidence=0,
            reasons=["无法获取日线数据（离线或数据源失败）。"],
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            status_zh="数据不足，跳过规则判断。",
        )

    close = float(summary["close"])
    trend_ok, strong, tr_reasons = trend_flags(summary)
    trend_score = _score_trend(trend_ok, strong)
    reasons.extend(tr_reasons[:4])

    qqq_s = bars_summary(load_daily_bars("QQQ")) or {}
    q_ret = float(qqq_s.get("ret_63d") or 0.0)
    s_ret = float(summary.get("ret_63d") or 0.0)
    rs = (s_ret - q_ret) * 100.0
    reasons.append(f"相对强度（约 3 月 vs QQQ）：{rs:+.1f}%（百分点）。")

    pivot = pivot_level(summary)
    sm = setup_metrics(
        summary,
        near_pct=cfg["near_pivot_pct"],
        extended_pct=cfg["extended_pct"],
        vol_ratio=cfg["volume_confirm_ratio"],
    )
    reasons.append(
        f"Pivot 代理（近 20 日高）≈ {pivot:,.2f}，"
        f"距 pivot {sm['dist_to_pivot_pct']:+.1f}%。"
    )

    stop_plan = planned_stop(summary, max_loss_pct=cfg["max_loss_pct"])
    if avg_cost and avg_cost > 0:
        cost_stop = float(avg_cost) * (1.0 - cfg["max_loss_pct"])
        stop_plan = max(stop_plan, cost_stop)

    target = pivot * cfg["target_pivot_mult"] if pivot else close * 1.06
    rr = reward_risk(close, stop_plan, target)
    risk_pct = ((close - stop_plan) / close * 100.0) if close > stop_plan else None

    rsi_v = summary.get("rsi14")
    fomo = ""
    if sm["extended"] and rsi_v is not None and float(rsi_v) > 68.0:
        fomo = "情绪提醒：价格已明显偏离 pivot 且 RSI 偏高，警惕 FOMO 追高。"

    shares = None
    if account_equity and account_equity > 0 and close > stop_plan:
        shares = suggest_shares(
            account_equity=account_equity,
            risk_budget_pct=cfg["risk_per_trade_pct"],
            entry=close,
            stop=stop_plan,
        )

    # --- Held position branch ---
    if avg_cost is not None and avg_cost > 0:
        if close < stop_plan - 1e-6:
            return TradeDisciplineSignal(
                ticker=sym,
                action="STOP_TRIGGERED",
                action_zh=ACTION_ZH["STOP_TRIGGERED"],
                confidence=88,
                reasons=reasons
                + [
                    f"现价低于计划止损参考 {stop_plan:,.2f}（含成本/摆动规则，非券商指令）。",
                    "Douglas：亏损在计划内是成本；若规则仍成立，按计划处理而非摊平。",
                ],
                entry=close,
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="止损计划触发：原假设失效，优先复核新闻/基本面再决定是否离场。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo,
                suggest_shares=shares,
            )

        m50 = summary.get("ma50")
        vol = float(summary.get("vol") or 0.0)
        v50 = float(summary.get("vol50") or 1.0) or 1.0
        if m50 and close < float(m50) * 0.985 and vol > v50 * 1.25:
            return TradeDisciplineSignal(
                ticker=sym,
                action="REDUCE_OR_EXIT",
                action_zh=ACTION_ZH["REDUCE_OR_EXIT"],
                confidence=72,
                reasons=reasons
                + ["跌破 MA50 附近且放量：动能转弱信号，考虑减仓或收紧跟踪止损。"],
                entry=close,
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="趋势转弱：不是「一定卖」，而是提醒用纪律处理风险。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo,
                suggest_shares=shares,
            )

        if sm["extended"]:
            return TradeDisciplineSignal(
                ticker=sym,
                action="HOLD",
                action_zh=ACTION_ZH["HOLD"],
                confidence=62,
                reasons=reasons
                + [
                    "已持有：价格相对 pivot 偏延伸；核心仓可按原趋势持有，但「加仓」不适合追价。",
                ],
                entry=close,
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="持有中：注意 EXTENDED 语境下避免 FOMO 加码。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo or "好股票也可能离好买点很远；新增资金优先等回踩或下一 setup。",
                suggest_shares=shares,
            )

        if trend_ok and summary.get("ma20") and close > float(summary["ma20"]):
            return TradeDisciplineSignal(
                ticker=sym,
                action="HOLD",
                action_zh=ACTION_ZH["HOLD"],
                confidence=70,
                reasons=reasons + ["趋势结构仍在：未触发止损/破位规则，偏向持有观察。"],
                entry=close,
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="持有：可用 MA20/MA50 做利润保护参考（非自动卖单）。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo,
                suggest_shares=shares,
            )

        return TradeDisciplineSignal(
            ticker=sym,
            action="WATCH",
            action_zh=ACTION_ZH["WATCH"],
            confidence=55,
            reasons=reasons + ["已持有但未满足强趋势持有条件：维持观察，按止损/均线纪律执行。"],
            entry=close,
            stop=round(stop_plan, 2),
            risk_pct=risk_pct,
            reward_risk=rr,
            pivot=round(pivot, 2),
            status_zh="观察：等待结构重新走强或触发减仓规则。",
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            trend_score=trend_score,
            rs_vs_qqq_pct=rs,
            fomo_hint=fomo,
            suggest_shares=shares,
        )

    # --- Not held (setup / entry discipline) ---
    if not trend_ok:
        return TradeDisciplineSignal(
            ticker=sym,
            action="AVOID",
            action_zh=ACTION_ZH["AVOID"],
            confidence=40,
            reasons=reasons + ["趋势模板未通过：中短线顺势框架下，优先不新开追涨仓。"],
            entry=round(close, 2),
            stop=round(stop_plan, 2),
            risk_pct=risk_pct,
            reward_risk=rr,
            pivot=round(pivot, 2),
            status_zh="环境或个股结构不配合，宁可错过。",
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            trend_score=trend_score,
            rs_vs_qqq_pct=rs,
            fomo_hint=fomo,
            suggest_shares=shares,
        )

    if sm["extended"]:
        return TradeDisciplineSignal(
            ticker=sym,
            action="EXTENDED_DONT_CHASE",
            action_zh=ACTION_ZH["EXTENDED_DONT_CHASE"],
            confidence=68,
            reasons=reasons
            + [
                "价格相对 pivot 过远：好股票也可能不是好买点；风险收益比变差。",
            ],
            entry=round(close, 2),
            stop=round(stop_plan, 2),
            risk_pct=risk_pct,
            reward_risk=rr,
            pivot=round(pivot, 2),
            status_zh="别追：等待回踩、整理或下一个 pivot。",
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            trend_score=trend_score,
            rs_vs_qqq_pct=rs,
            fomo_hint=fomo or "情绪提醒：延伸后追涨多为 FOMO，系统刻意压低买点优先级。",
            suggest_shares=shares,
        )

    if regime.label == "risk_off":
        if sm["near_pivot"] or sm["breakout"]:
            return TradeDisciplineSignal(
                ticker=sym,
                action="WATCH",
                action_zh=ACTION_ZH["WATCH"],
                confidence=58,
                reasons=reasons
                + [regime.detail, "大盘 risk-off：即使有个股结构，也只给到观察/等待，不给「突破追涨」。"],
                entry=round(close, 2),
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="可关注：等大盘与个股共振再谈触发买点。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo,
                suggest_shares=shares,
            )

    if sm["vcp_like"] and not sm["breakout"]:
        reasons.append("缩量整理/VCP-lite 提示：更像「形态形成中」，等待 pivot 放量突破确认。")

    if sm["near_pivot"] and not sm["breakout"]:
        conf = 66 + min(12, max(0, int(rs / 3)))
        return TradeDisciplineSignal(
            ticker=sym,
            action="NEAR_ENTRY",
            action_zh=ACTION_ZH["NEAR_ENTRY"],
            confidence=min(92, conf),
            reasons=reasons + ["接近 pivot：关注放量突破与风险收益比，不提前重仓。"],
            entry=round(close, 2),
            stop=round(stop_plan, 2),
            risk_pct=risk_pct,
            reward_risk=rr,
            pivot=round(pivot, 2),
            status_zh="接近买点：等确认，不是「提前梭哈」。",
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            trend_score=trend_score,
            rs_vs_qqq_pct=rs,
            fomo_hint=fomo,
            suggest_shares=shares,
        )

    if sm["breakout"] and sm["volume_confirm"]:
        if rr is not None and rr < cfg["min_reward_risk"]:
            return TradeDisciplineSignal(
                ticker=sym,
                action="WATCH",
                action_zh=ACTION_ZH["WATCH"],
                confidence=60,
                reasons=reasons
                + [
                    f"突破+放量，但风险收益比约 {rr:.2f} < {cfg['min_reward_risk']:.1f}：追入性价比一般。",
                ],
                entry=round(close, 2),
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="有突破迹象，但盈亏比不够：宁可等回踩或更高目标确认。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo,
                suggest_shares=shares,
            )
        if regime.label == "caution":
            return TradeDisciplineSignal(
                ticker=sym,
                action="SETUP_FORMING",
                action_zh=ACTION_ZH["SETUP_FORMING"],
                confidence=64,
                reasons=reasons
                + ["突破放量在「谨慎」大盘环境下：降级为形态/确认阶段，避免默认满仓追。"],
                entry=round(close, 2),
                stop=round(stop_plan, 2),
                risk_pct=risk_pct,
                reward_risk=rr,
                pivot=round(pivot, 2),
                status_zh="突破确认质量一般：先看大盘与次日持续性。",
                market_regime=regime.label,
                market_regime_zh=regime.zh,
                trend_score=trend_score,
                rs_vs_qqq_pct=rs,
                fomo_hint=fomo,
                suggest_shares=shares,
            )
        return TradeDisciplineSignal(
            ticker=sym,
            action="BUY_TRIGGERED",
            action_zh=ACTION_ZH["BUY_TRIGGERED"],
            confidence=min(90, 72 + int(rs / 4)),
            reasons=reasons
            + [
                "突破 pivot 且量能放大：满足简化「触发」条件（仍需你自行下单与仓位上限）。",
                f"参考风险收益比 ≈ {rr:.2f}（目标取 pivot×{cfg['target_pivot_mult']:.2f} 的粗近似）。" if rr else "风险收益比暂无法可靠估计。",
            ],
            entry=round(close, 2),
            stop=round(stop_plan, 2),
            risk_pct=risk_pct,
            reward_risk=rr,
            pivot=round(pivot, 2),
            status_zh="计划内触发：不等于无脑买；请核对账户风险与新闻。",
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            trend_score=trend_score,
            rs_vs_qqq_pct=rs,
            fomo_hint=fomo,
            suggest_shares=shares,
        )

    if sm["vcp_like"]:
        return TradeDisciplineSignal(
            ticker=sym,
            action="SETUP_FORMING",
            action_zh=ACTION_ZH["SETUP_FORMING"],
            confidence=58,
            reasons=reasons,
            entry=round(close, 2),
            stop=round(stop_plan, 2),
            risk_pct=risk_pct,
            reward_risk=rr,
            pivot=round(pivot, 2),
            status_zh="形态形成中：等待更清晰的 pivot / 成交量信号。",
            market_regime=regime.label,
            market_regime_zh=regime.zh,
            trend_score=trend_score,
            rs_vs_qqq_pct=rs,
            fomo_hint=fomo,
            suggest_shares=shares,
        )

    return TradeDisciplineSignal(
        ticker=sym,
        action="WATCH",
        action_zh=ACTION_ZH["WATCH"],
        confidence=52,
        reasons=reasons + ["暂未达到接近买点/突破确认；保持观察名单纪律。"],
        entry=round(close, 2),
        stop=round(stop_plan, 2),
        risk_pct=risk_pct,
        reward_risk=rr,
        pivot=round(pivot, 2),
        status_zh="可关注：把规则交给时间，不因单日波动改系统。",
        market_regime=regime.label,
        market_regime_zh=regime.zh,
        trend_score=trend_score,
        rs_vs_qqq_pct=rs,
        fomo_hint=fomo,
        suggest_shares=shares,
    )

