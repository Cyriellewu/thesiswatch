"""把 Murphy / Minervini(VCP-lite) / Douglas / Elder 与页面「区间」合成一张可解释的决策视图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from signals.indicators import bars_summary, load_daily_bars
from signals.market_regime import compute_market_regime
from signals.setups import pivot_level, setup_metrics
from signals.signal_engine import evaluate_trade_discipline
from signals.trend_template import trend_flags
from signals.vcp_probe import vcp_probe
from stock_picker.zone_context import instrument_kind

_ROOT = Path(__file__).resolve().parents[1]


def _signal_engine_cfg() -> dict[str, Any]:
    """与 `signals.signal_engine._load_engine_cfg` 同构，避免循环/旧文件缺导出。"""
    p = _ROOT / "config" / "settings.yaml"
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


@dataclass
class BookCardView:
    """单标的：四层书籍检查 + 系统是否允许把「在区间内」当作买点。"""

    symbol: str
    in_zone: bool
    system_buyable: bool
    system_buy_blockers: list[str] = field(default_factory=list)
    instrument_kind: str = "steady"
    murphy_setup_zh: str = ""
    zone_rationale_zh: str = ""
    invalidation_zh: str = ""
    first_tranche_usd: float | None = None
    first_tranche_hint: str = ""
    rs_vs_qqq_ppt: float = 0.0
    rs_vs_spy_ppt: float = 0.0
    rs_gate_ok: bool = True
    murphy_lines: list[str] = field(default_factory=list)
    minervini_lines: list[str] = field(default_factory=list)
    minervini_template_ok: bool = False
    vcp_label: str = ""
    vcp_detail_lines: list[str] = field(default_factory=list)
    vcp_lines: list[str] = field(default_factory=list)
    douglas_lines: list[str] = field(default_factory=list)
    elder_lines: list[str] = field(default_factory=list)
    regime_label: str = ""
    regime_zh: str = ""
    discipline_action: str = ""
    discipline_action_zh: str = ""
    entry: float | None = None
    stop: float | None = None
    risk_per_share: float | None = None
    reward_risk: float | None = None
    suggest_shares: int | None = None
    max_planned_loss_usd: float | None = None
    pivot: float | None = None
    fomo_hint: str = ""
    book_alert_tags: list[str] = field(default_factory=list)


def _first_tranche_usd(account_equity: float) -> float:
    return min(500.0, max(50.0, float(account_equity) * 0.004))


def _murphy_setup_zh(
    *,
    close: float,
    summary: dict[str, Any],
    swing: float,
    piv: float,
) -> str:
    pieces: list[str] = []
    m50 = summary.get("ma50")
    m200 = summary.get("ma200")
    if m50 and close > 0 and abs(close - float(m50)) / close <= 0.04:
        pieces.append("靠近 MA50")
    if m200 and close > 0:
        m200f = float(m200)
        if m200f * 0.97 <= close <= m200f * 1.06:
            pieces.append("贴近 MA200 一带")
    if swing > 0 and close > 0 and abs(close - swing) / close <= 0.04:
        pieces.append("贴近近 20 日摆动低（支撑代理）")
    if piv > 0 and close > 0 and close >= piv * 0.94:
        pieces.append("接近 pivot 压力区（易震荡）")
    if not pieces:
        pieces.append("typed 价格带为主，未见典型「突破回踩 / 明确平台下沿」")
    return " · ".join(pieces)


def _dist_52w_high_pct(h: Any, close: float) -> float | None:
    if h is None or getattr(h, "empty", True) or "Close" not in h.columns:
        return None
    tail = min(252, len(h))
    if tail < 40:
        return None
    hi = float(h["Close"].astype(float).tail(tail).max())
    if hi <= 0 or close <= 0:
        return None
    return (hi - close) / hi * 100.0


def build_book_card_view(
    row: Any,
    *,
    account_equity: float = 25_000.0,
) -> BookCardView:
    sym = str(getattr(row, "symbol", "") or "").strip().upper()
    kind = instrument_kind(sym, str(getattr(row, "bucket", "")), str(getattr(row, "stock_type", "")))
    zones = getattr(row, "zones", None)
    if not sym or zones is None:
        return BookCardView(symbol=sym or "?", in_zone=False, system_buyable=False, system_buy_blockers=["数据不完整。"])

    try:
        rec_lo = float(getattr(zones, "recommended_low", 0.0) or 0.0)
        rec_hi = float(getattr(zones, "recommended_high", 0.0) or 0.0)
    except (TypeError, ValueError):
        return BookCardView(symbol=sym, in_zone=False, system_buyable=False, system_buy_blockers=["区间数据无效。"])

    try:
        return _build_book_card_view_core(row, sym=sym, kind=kind, zones=zones, rec_lo=rec_lo, rec_hi=rec_hi, account_equity=account_equity)
    except Exception as exc:
        return BookCardView(
            symbol=sym,
            in_zone=False,
            system_buyable=False,
            instrument_kind=kind,
            system_buy_blockers=[f"书籍检查异常（已降级）：{type(exc).__name__}: {exc}"],
            zone_rationale_zh="书籍/模板计算被内部错误打断；typed 区间仍以卡片下方为准。",
            invalidation_zh="可检查网络、日线数据与终端日志后重试。",
            murphy_lines=[str(exc)],
            minervini_lines=["Trend Template：未计算（上游错误）。"],
            discipline_action_zh="未计算",
            regime_label="?",
            regime_zh="未计算",
        )


def _build_book_card_view_core(
    row: Any,
    *,
    sym: str,
    kind: str,
    zones: Any,
    rec_lo: float,
    rec_hi: float,
    account_equity: float,
) -> BookCardView:
    h = load_daily_bars(sym, "2y")
    summary = bars_summary(h) if h is not None and not getattr(h, "empty", True) else {}
    regime = compute_market_regime()
    cfg = _signal_engine_cfg()

    if not summary:
        sig = evaluate_trade_discipline(sym, account_equity=account_equity)
        return BookCardView(
            symbol=sym,
            in_zone=False,
            system_buyable=False,
            system_buy_blockers=["无法拉取日线，跳过书籍检查。"],
            instrument_kind=kind,
            regime_label=sig.market_regime,
            regime_zh=sig.market_regime_zh,
            discipline_action=sig.action,
            discipline_action_zh=sig.action_zh,
        )

    px = float(getattr(row, "price", None) or summary.get("close") or 0.0)
    in_zone = rec_lo <= px <= rec_hi and rec_hi > 0

    sig = evaluate_trade_discipline(sym, account_equity=account_equity, summary_precalc=summary)
    trend_ok, strong, tr_reasons = trend_flags(summary)
    sm = setup_metrics(
        summary,
        near_pct=float(cfg["near_pivot_pct"]),
        extended_pct=float(cfg["extended_pct"]),
        vol_ratio=float(cfg["volume_confirm_ratio"]),
    )
    vcp = vcp_probe(h)

    close = float(summary["close"])
    swing = float(summary.get("swing_low_20") or 0.0)
    piv = pivot_level(summary)
    vol = float(summary.get("vol") or 0.0)
    v50 = float(summary.get("vol50") or 1.0) or 1.0
    vol_vs = vol / v50 if v50 else 1.0
    if vol_vs > 1.2:
        vol_zh = "放量"
    elif vol_vs < 0.85:
        vol_zh = "缩量"
    else:
        vol_zh = "量能接近均量"

    murphy_setup_zh = _murphy_setup_zh(close=close, summary=summary, swing=swing, piv=piv)
    d52 = _dist_52w_high_pct(h, close)
    murphy: list[str] = [
        f"**结构/位置摘要**：{murphy_setup_zh}",
        "趋势（Minervini 模板）：" + ("通过" if trend_ok else "未通过") + ("（偏强）" if strong else "") + "。",
        *tr_reasons[:4],
        f"支撑代理（近 20 日摆动低）：约 ${swing:,.2f}。" if swing else "支撑代理：数据不足。",
        f"压力 / pivot 代理（近 20 日高）：约 ${piv:,.2f}。" if piv else "压力代理：数据不足。",
        f"成交量：相对 50 日均量约 {vol_vs:.2f}x（{vol_zh}）。",
    ]
    if d52 is not None:
        murphy.append(f"距近 {min(252, len(h) if h is not None else 0)} 日收盘高点约 {d52:.1f}%（离前高越远，突破叙事越弱）。")

    if sm["volume_breakdown"]:
        murphy.append("**放量跌破 MA50 一带**：更像动能转弱，Murphy 语境下慎接刀。")
    elif in_zone and trend_ok and vol_vs < 1.0:
        murphy.append("**回踩量能未放大**：偏「健康回调」一侧（仍需结合模板与盈亏比）。")
    elif in_zone:
        murphy.append("**在 typed 区间内**：可能是整理也可能是弱势震荡，需结合模板与大盘。")

    spy_s = bars_summary(load_daily_bars("SPY")) or {}
    s_ret = float(summary.get("ret_63d") or 0.0)
    q_ret = float((bars_summary(load_daily_bars("QQQ")) or {}).get("ret_63d") or 0.0)
    spy_ret = float(spy_s.get("ret_63d") or 0.0)
    rsq = (s_ret - q_ret) * 100.0
    rss = (s_ret - spy_ret) * 100.0
    m50v = summary.get("ma50")
    m200v = summary.get("ma200")
    m50f = float(m50v) if m50v is not None else 0.0
    m200f = float(m200v) if m200v is not None else 0.0
    slope200 = float(summary.get("ma200_slope") or 0.0)
    min_lines = [
        "— Trend Template（逐项）—",
        f"Close > MA50：{'是' if m50v is not None and close > m50f else '否'}",
        f"Close > MA200：{'是' if m200v is not None and close > m200f else '否'}",
        f"MA50 > MA200：{'是' if m50v is not None and m200v is not None and m50f > m200f else '否'}",
        f"MA200 斜率为正：{'是' if slope200 > 0 else '否'}",
        f"相对强度 3m vs QQQ：{rsq:+.1f}%（系统要求 **>0**）",
        f"相对强度 3m vs SPY：{rss:+.1f}%（系统要求 **>0**）",
    ]

    t_score = int(getattr(row, "t_score", 0) or 0)
    blockers: list[str] = []
    if kind == "etf":
        blockers.append("ETF：买点语义用「每周定投 + MA200 额外加仓带」，不用 typed 区间当系统买点。")
    if not in_zone:
        blockers.append("现价不在 typed 推荐/观察带内。")
    if t_score < 4:
        blockers.append(f"页面趋势分 T={t_score}<4：仅观察，不把 typed 带当「买入区间」。")
    if not trend_ok:
        blockers.append("Minervini 趋势模板未通过：不允许系统级「可分批买点」。")
    if regime.label == "risk_off":
        blockers.append(f"大盘 {regime.zh}：环境 risk-off，关闭系统级分批买点。")
    if sm["volume_breakdown"]:
        blockers.append("放量跌破 MA50 一带：不接「跌进区间就买」。")
    rr = sig.reward_risk
    if rr is None or rr < float(cfg["min_reward_risk"]):
        blockers.append(
            f"风险收益比不足（约 {rr if rr is not None else '—'} ，门槛 ≥ {float(cfg['min_reward_risk']):.1f}）。"
        )
    if kind != "etf":
        if rsq <= 0:
            blockers.append("约 3 月相对强度未跑赢 QQQ（≤0）：不满足强势股相对强度门。")
        if rss <= 0:
            blockers.append("约 3 月相对强度未跑赢 SPY（≤0）：不满足强势股相对强度门。")

    system_buyable = (kind != "etf") and in_zone and (len(blockers) == 0)
    rs_gate_ok = (kind == "etf") or (rsq > 0 and rss > 0)

    pcts = list(vcp.get("pullbacks_pct") or [])
    vcp_detail_lines: list[str] = []
    if len(pcts) >= 3:
        vr = vcp.get("vol_ratio_tail_vs_head")
        vr_txt = f"{float(vr):.2f}" if isinstance(vr, (int, float)) else "—"
        vcp_detail_lines.append(
            f"**VCP 启发式**：三次段内振幅 **{pcts[0]:.1f}% → {pcts[1]:.1f}% → {pcts[2]:.1f}%**；"
            f"量能尾/首 ≈ **{vr_txt}**（<0.85 视为收缩）。**结论**：{vcp.get('label') or '—'}。"
        )
    vcp_detail_lines.extend(list(vcp.get("lines") or []))

    ft_usd = round(_first_tranche_usd(account_equity), 0)
    first_tranche_hint = (
        f"首笔试探约 **${ft_usd:,.0f}**（示意：权益×0.4%，$50–$500 夹逼），"
        "不是梭哈许可；若不能接受计划止损下亏损，则不下单。"
    )

    stop_v = sig.stop
    entry_v = sig.entry
    rps = None
    if entry_v is not None and stop_v is not None and entry_v > stop_v:
        rps = round(entry_v - stop_v, 4)
    max_loss = None
    if sig.suggest_shares is not None and rps is not None and sig.suggest_shares > 0:
        max_loss = round(float(sig.suggest_shares) * float(rps), 2)

    comf_z = float(getattr(zones, "comfortable_buy", 0.0) or 0.0)
    if system_buyable:
        zone_rationale_zh = (
            f"系统门通过：typed 带内 + Trend Template + 大盘（{regime.zh}）+ 相对强度跑赢 QQQ/SPY + 未放量破位 + "
            f"R:R≥{float(cfg['min_reward_risk']):.1f}。结构：{murphy_setup_zh}"
        )
    elif in_zone:
        zone_rationale_zh = f"typed 带内；{murphy_setup_zh}。未通过：{'；'.join(blockers[:6])}"
    else:
        zone_rationale_zh = f"现价不在 typed 主带；{murphy_setup_zh}。"

    inv_bits: list[str] = []
    if stop_v is not None:
        inv_bits.append(f"计划止损 ${float(stop_v):,.2f}")
    inv_bits.append(f"typed 下沿 ${rec_lo:,.2f}")
    if comf_z > 0:
        inv_bits.append(f"舒适回踩 ${comf_z:,.2f}")
    if swing > 0:
        inv_bits.append(f"摆动低 ${swing:,.2f}")
    invalidation_zh = "若有效跌破 " + " / ".join(inv_bits[:4]) + "，系统买点假设先作废；放量破位勿机械接刀。"

    elder: list[str] = [
        f"**Elder · Money**：{first_tranche_hint}",
    ]
    if entry_v is not None and stop_v is not None:
        elder.append(f"参考入场（现价代理）：**${entry_v:,.2f}** · 计划止损参考：**${stop_v:,.2f}**。")
    if rps is not None:
        elder.append(f"每股承担风险约：**${rps:,.2f}**（计划内，非保证）。")
    if rr is not None:
        elder.append(f"粗算风险收益比（目标 pivot×系数）：**{rr:.2f}**。")
    if sig.suggest_shares is not None:
        elder.append(
            f"按账户单笔风险 {float(cfg['risk_per_trade_pct']) * 100:.1f}% 估算股数：**{int(sig.suggest_shares)} 股**"
            + (f" · 计划最大亏损约 **${max_loss:,.2f}**" if max_loss is not None else "")
            + "。"
        )
    elder.append("Elder：先接受亏损再下单；金额层不满足就不要放大仓位。")

    douglas: list[str] = [
        "Douglas：你不是在「买便宜」，而是在执行事先写好的规则；规则错了可以改，但不要盘中临时改口安慰自己。",
        f"把首笔当成**计划**的一部分：示意金额约 **${ft_usd:,.0f}**，不是「买到爽」的预算上限。",
    ]
    if sig.fomo_hint:
        douglas.append(sig.fomo_hint)
    if sm["extended"]:
        douglas.append("已明显延伸离 pivot：追高多为 FOMO，不是系统买点。")
    if max_loss is not None:
        douglas.append(f"若**无法接受**大约 **${max_loss:,.2f}** 的计划内亏损，就不要为证明自己对而硬下单。")
    if sig.action == "STOP_TRIGGERED":
        douglas.append("已触发计划止损语境：继续持有若只为摊平，属于改规则，不是执行原系统。")

    tags: list[str] = []
    if sm["extended"]:
        tags.append("EXTENDED_DONT_CHASE")
    if sm["near_pivot"] and trend_ok and not sm["extended"]:
        tags.append("NEAR_PIVOT")
    if sm["breakout"] and sm["volume_confirm"]:
        tags.append("BREAKOUT_WITH_VOLUME")
    if system_buyable and in_zone:
        tags.append("ENTER_BUY_ZONE")
    if kind == "etf":
        ma200 = float(getattr(row, "ma200", 0.0) or 0.0)
        if ma200 > 0:
            ex_lo, ex_hi = ma200 * 0.90, ma200 * 1.03
            if ex_lo <= px <= ex_hi:
                tags.append("ENTER_EXTRA_DCA_ZONE")

    return BookCardView(
        symbol=sym,
        in_zone=in_zone,
        system_buyable=system_buyable,
        system_buy_blockers=blockers if not system_buyable else [],
        instrument_kind=kind,
        murphy_setup_zh=murphy_setup_zh,
        zone_rationale_zh=zone_rationale_zh,
        invalidation_zh=invalidation_zh,
        first_tranche_usd=ft_usd,
        first_tranche_hint=first_tranche_hint,
        rs_vs_qqq_ppt=rsq,
        rs_vs_spy_ppt=rss,
        rs_gate_ok=rs_gate_ok,
        murphy_lines=murphy,
        minervini_lines=min_lines,
        minervini_template_ok=trend_ok,
        vcp_label=str(vcp.get("label") or ""),
        vcp_detail_lines=vcp_detail_lines,
        vcp_lines=list(vcp.get("lines") or []),
        douglas_lines=douglas,
        elder_lines=elder,
        regime_label=regime.label,
        regime_zh=regime.zh,
        discipline_action=sig.action,
        discipline_action_zh=sig.action_zh,
        entry=entry_v,
        stop=stop_v,
        risk_per_share=rps,
        reward_risk=rr,
        suggest_shares=sig.suggest_shares,
        max_planned_loss_usd=max_loss,
        pivot=sig.pivot,
        fomo_hint=sig.fomo_hint or "",
        book_alert_tags=tags,
    )
