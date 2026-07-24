from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

from data_layer.macro_data import snapshot_macro
from data_layer.market_data import fetch_quotes
from data_layer import portfolio_analytics as pa
from signals.market_regime import compute_market_regime
from signals.signal_engine import evaluate_trade_discipline
from tasks.weekly_dca import get_weekly_dca_status
from ui.radar_widget import render_major_moves

equity_curve_daily = pa.equity_curve_daily
load_positions = pa.load_positions
sector_for_symbol = pa.sector_for_symbol

_CORE_ETF = {"VOO", "VTI", "SPY", "QQQ", "QQQM"}
_QUALITY_GROWTH = {"MSFT", "GOOGL", "GOOG", "META", "NVDA", "AVGO", "PANW", "AMZN", "AAPL"}
_DEFENSIVE = {"COST", "WMT", "PG", "KO", "JNJ", "UNH", "V", "MA", "JPM", "NEE", "XLU", "XLV", "XLF"}
_SATELLITE = {"IREN", "TSLA", "OKLO", "COIN", "HOOD", "SOFI", "MSTR", "RIVN"}
_TECH_HINT = {
    "Technology",
    "Semiconductors",
    "Electronic Technology",
    "Communication Services",
    "Internet",
}


def _cash_fallback(wl_path: Path) -> float:
    try:
        raw = yaml.safe_load(wl_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return 0.0
    for k in ("account_cash_usd", "cash_usd", "cash"):
        v = raw.get(k)
        if v is None:
            continue
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            continue
    return 0.0


def _role_of(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    if s in _CORE_ETF:
        return "核心底仓"
    if s in _QUALITY_GROWTH:
        return "高质量成长"
    if s in _DEFENSIVE:
        return "防御稳定"
    if s in _SATELLITE:
        return "高波动卫星"
    return "观察仓"


def _calc_health(
    df: pd.DataFrame,
    *,
    total_mv: float,
    cash_ratio: float,
    role_weight: dict[str, float],
    tech_weight: float,
) -> tuple[str, list[str]]:
    flags: list[str] = []
    max_pos = float(df["weight"].max()) if (not df.empty and total_mv > 0) else 0.0
    sat_weight = role_weight.get("高波动卫星", 0.0)
    def_weight = role_weight.get("防御稳定", 0.0)

    if tech_weight > 0.60:
        flags.append("科技偏重")
    if def_weight < 0.10:
        flags.append("防御不足")
    if max_pos > 0.15:
        flags.append("单票偏重")
    if sat_weight > 0.10:
        flags.append("高波动偏高")
    if cash_ratio > 0.45:
        flags.append("现金偏高，可分批投入")
    if cash_ratio < 0.15:
        flags.append("现金偏低，弹药不足")

    if not flags:
        return "结构均衡", []
    return " / ".join(flags[:3]), flags


def _insert_manual_alert(
    *,
    category: str,
    symbol: str | None,
    title: str,
    what: str,
    why: str,
    risk: str,
) -> bool:
    try:
        from db.client import get_conn  # noqa: PLC0415
    except Exception:
        return False
    occ = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sym = (symbol or "").upper().strip() or None
    day_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    dedupe = f"manual:{category}:{sym or 'portfolio'}:{title}:{day_key}"
    w = get_conn(read_only=False)
    try:
        w.execute(
            """INSERT OR IGNORE INTO alerts
               (level, category, symbol, title, what_happened, why_matters, risk, dedupe_key, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("attention", category, sym, title, what, why, risk, dedupe, occ),
        )
        w.commit()
        return True
    except Exception:
        return False
    finally:
        w.close()


def _status_and_action(row: pd.Series, max_day_pnl: float, min_day_pnl: float) -> tuple[str, str]:
    tags: list[str] = []
    actions: list[str] = []
    w = float(row.get("weight", 0.0))
    day_pct = float(row.get("day_chg_pct", 0.0))
    tr_pct = float(row.get("total_return_pct", 0.0))
    role = str(row.get("role") or "")
    sym = str(row.get("ticker") or "").upper()
    day_pnl = float(row.get("day_pnl_usd", 0.0))

    if w > 0.20:
        tags.append("仓位过重")
        actions.append("优先评估减仓纪律")
    elif w > 0.15:
        tags.append("仓位较高")
    elif w < 0.03:
        tags.append("仓位很小")

    if abs(day_pct) >= 5:
        tags.append("波动偏大")
        actions.append("设单日波动提醒")
    if tr_pct >= 25:
        tags.append("强势持有")
        actions.append("设回撤保护提醒")
    elif tr_pct <= -5:
        tags.append("低于成本")
        actions.append("检查逻辑是否变化")
    else:
        tags.append("正常持有")

    if day_pnl == max_day_pnl and day_pnl > 0:
        tags.append("今日贡献最大")
    if day_pnl == min_day_pnl and day_pnl < 0:
        tags.append("今日拖累最大")
    if sym in _CORE_ETF:
        tags.append("ETF底仓")
        actions.append("继续定投为主")
    if role == "高波动卫星":
        tags.append("高波动卫星")
        if w > 0.08:
            tags.append("卫星仓位偏高")
            actions.append("降低卫星仓位占比")

    return " / ".join(dict.fromkeys(tags)), "；".join(dict.fromkeys(actions)) or "维持持有，按纪律观察"


def _render_discipline_engine(df: pd.DataFrame, *, account_total: float) -> None:
    """Murphy / Minervini / Douglas / Elder — rule stack, not buy/sell orders."""
    with st.expander("📐 中短期纪律引擎（Signal Engine）", expanded=False):
        st.caption(
            "链路：**大盘环境 → 趋势模板 → pivot/量能 → 风险止损 → 心理提示**。输出为纪律标签，"
            "不是「预测会涨」；**不会**向券商下单。"
        )
        regime = compute_market_regime()
        st.markdown(f"**大盘环境**：{regime.zh}（`{regime.label}`）")
        st.caption(regime.detail)
        if df.empty:
            return
        out_rows: list[dict] = []
        for _, r in df.iterrows():
            sym = str(r["ticker"]).upper()
            ac = float(r.get("avg_cost_per_share") or 0.0)
            sig = evaluate_trade_discipline(sym, avg_cost=ac if ac > 0 else None, account_equity=account_total)
            out_rows.append(
                {
                    "标的": sym,
                    "纪律状态": sig.action_zh,
                    "信心": sig.confidence,
                    "趋势分": sig.trend_score,
                    "RS vs QQQ(3m, ppt)": round(sig.rs_vs_qqq_pct, 1),
                    "Pivot": sig.pivot,
                    "参考止损": sig.stop,
                    "R:R": round(sig.reward_risk, 2) if sig.reward_risk is not None else None,
                    "1%风险建议股数": sig.suggest_shares,
                    "提示": (sig.status_zh + ("；" + sig.fomo_hint if sig.fomo_hint else "")),
                }
            )
        st.dataframe(pd.DataFrame(out_rows), hide_index=True, use_container_width=True)
        st.caption("建议股数按 `settings.yaml → signal_engine.risk_per_trade_pct` 粗算，实际请结合流动性、单票上限与税费。")


def _render_compact_rows(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("暂无持仓可展示。")
        return
    max_day_pnl = float(df["day_pnl_usd"].max()) if not df.empty else 0.0
    min_day_pnl = float(df["day_pnl_usd"].min()) if not df.empty else 0.0

    st.markdown("#### 持仓状态")
    hdr = st.columns([1.1, 1.0, 0.9, 1.0, 0.9, 1.7, 0.8])
    labels = ["标的", "角色", "仓位", "今日表现", "总收益", "状态 / 建议", "提醒"]
    for c, label in zip(hdr, labels):
        c.caption(label)

    for _, r in df.iterrows():
        sym = str(r["ticker"])
        status, action = _status_and_action(r, max_day_pnl, min_day_pnl)
        cols = st.columns([1.1, 1.0, 0.9, 1.0, 0.9, 1.7, 0.8])
        cols[0].markdown(f"`{sym}`")
        cols[1].write(str(r["role"]))
        cols[2].write(f"{float(r['weight']) * 100:.1f}%")
        cols[3].write(f"{float(r['day_chg_pct']):+.2f}% / ${float(r['day_pnl_usd']):+,.2f}")
        cols[4].write(f"{float(r['total_return_pct']):+.2f}%")
        cols[5].write(f"{status}｜{action}")
        with cols[6].popover("设提醒"):
            preset = st.selectbox(
                "预设",
                [
                    "单日上涨超过 X%",
                    "单日下跌超过 X%",
                    "总收益超过 +X%",
                    "总收益跌破 -X%",
                    "跌回成本价提醒",
                    "仓位占比超过 X%",
                    "跌破 50 日均线提醒",
                    "跌破 200 日均线提醒",
                ],
                key=f"preset_{sym}",
            )
            threshold = st.number_input("阈值 X", value=5.0, step=0.5, key=f"th_{sym}")
            note = st.text_input("备注", key=f"note_{sym}", placeholder="可选：例如仅工作时段提醒")
            if st.button("保存提醒", key=f"save_alert_{sym}"):
                ok = _insert_manual_alert(
                    category="holding_rule",
                    symbol=sym,
                    title=f"[持仓提醒] {sym} - {preset}",
                    what=f"阈值={threshold}; 备注={note}",
                    why="用于持仓纪律提醒，避免情绪化操作。",
                    risk="提醒触发不等于必须交易，需结合仓位与基本面复核。",
                )
                if ok:
                    st.success("已写入 alerts。")
                else:
                    st.error("写入失败，请检查数据库连接。")

        with st.expander(f"{sym} 明细", expanded=False):
            st.write(f"股数：{float(r['qty']):,.4f}")
            st.write(f"现价：${float(r['current_px']):,.4f}" if pd.notna(r["current_px"]) else "现价：—")
            st.write(f"市值：${float(r['market_value_usd']):,.2f}")
            st.write(f"成本：${float(r['cost_basis_usd']):,.2f}")
            st.write(f"每股成本：${float(r['avg_cost_per_share']):,.2f}")
            st.write(f"今日盈亏：${float(r['day_pnl_usd']):+,.2f}")
            st.write(f"总收益：{float(r['total_return_pct']):+.2f}%")


def render() -> None:
    render_major_moves(limit=4, compact=True)

    dca = get_weekly_dca_status()
    if dca.get("enabled"):
        with st.container(border=True):
            st.markdown("#### 💰 本周智能定投（QQQ $100 + VOO $50）")
            st.caption(
                f"周内回调 ≥{abs(float(dca.get('dip_pct_threshold') or 0.5)):.1f}% 时提醒买入；"
                f"若整周无回调，{dca.get('friday_force_et', '15:00')} ET 周五保底提醒。"
                " 需运行 `python -m jobs.daemon` 才会自动监控。"
            )
            cols = st.columns(len(dca.get("symbols") or []) or 1)
            for i, row in enumerate(dca.get("symbols") or []):
                sym = row.get("symbol", "")
                amt = row.get("weekly_usd")
                notified = row.get("notified")
                trigger = row.get("trigger")
                status = "✅ 本周已提醒" if notified else "⏳ 等待水坑或周五保底"
                if notified and trigger == "dip":
                    status = "✅ 周内回调已提醒"
                elif notified and trigger == "friday_fallback":
                    status = "✅ 周五保底已提醒"
                cols[i].metric(
                    sym,
                    f"${float(amt):,.0f}/周" if amt is not None else "—",
                    delta=status,
                )

    root = Path(__file__).resolve().parents[1]
    wl_path = root / "config" / "watchlist.yaml"

    positions = load_positions(wl_path)
    load_cash = getattr(pa, "load_account_cash_usd", None)
    cash_usd = float(load_cash(wl_path)) if callable(load_cash) else _cash_fallback(wl_path)
    if not positions:
        st.warning("请在 `config/watchlist.yaml` 配置 `ticker` · `qty` · `avg_cost_usd_per_share`。")
        return

    symbols = [p.ticker for p in positions]
    qty_by = {p.ticker: p.qty for p in positions}
    avg_by = {p.ticker: p.avg_cost_per_share for p in positions}

    quote_ttl = int(os.environ.get("ALPHA_QUOTE_CACHE_TTL", "60"))
    with st.expander("实盘 vs 虚拟 · 数据多久变一次？", expanded=False):
        st.markdown(
            f"""
- **谁管你的真仓？** 只有你自己。本页读的是 `config/watchlist.yaml`；改股数/成本后保存，再点页面才会重算。
- **现价从哪来？** 打开本 Tab 或切换 Tab 回来时会拉一次行情；同一批标的在 **约 {quote_ttl} 秒**内走本地缓存（环境变量 `ALPHA_QUOTE_CACHE_TTL`）。**没有**后台每 N 秒自动轮询。
- **「虚拟」会动我券商吗？** **不会。** `takeover-balanced` 只是在 SQLite 里复制这份持仓做模拟调仓；`fresh-balanced` 是另一笔 $5k 虚拟现金。成交写在 `agent_trades`，去 **「虚拟实验」** 看今天买了啥、原因是什么。
- **权益快照**（虚拟实验里的曲线）在每次 **市场脉冲**（手动点或 daemon）后落库，不是实时秒更。
            """.strip()
        )

    ntfy_col, _ = st.columns([1, 3])
    with ntfy_col:
        if st.button("推送持仓快照到 ntfy"):
            from push.notify import send_holdings_sync_ntfy

            if send_holdings_sync_ntfy():
                st.success("已发送到 ntfy。")
            else:
                st.error("失败：检查 `.env` 里的 `NTFY_TOPIC`。")

    with st.spinner(f"拉取 yfinance 行情（同批标的约 {quote_ttl}s 缓存）…"):
        quotes = fetch_quotes(symbols) or []
    qmap = {q.symbol.upper(): q for q in quotes}

    rows: list[dict] = []
    for p in positions:
        sym = p.ticker.upper()
        q = qmap.get(sym)
        px_i = float(q.px) if q else 0.0
        chg_pct = float(q.chg_pct) if q else 0.0
        mv = round(p.qty * px_i, 2) if px_i else 0.0
        cost = round(p.cost_basis_usd, 2)
        day_pnl = round(mv * (chg_pct / 100.0), 2)
        tr_pct = round((mv - cost) / cost * 100, 2) if cost > 0 else 0.0
        rows.append(
            {
                "ticker": sym,
                "qty": p.qty,
                "current_px": round(px_i, 4) if px_i else None,
                "market_value_usd": mv,
                "day_chg_pct": chg_pct,
                "day_pnl_usd": day_pnl,
                "cost_basis_usd": cost,
                "avg_cost_per_share": avg_by.get(sym, 0.0),
                "total_return_pct": tr_pct,
            }
        )

    df = pd.DataFrame(rows).sort_values("market_value_usd", ascending=False)

    total_mv = float(df["market_value_usd"].sum())
    total_cost = float(df["cost_basis_usd"].sum())
    total_pnl_abs = total_mv - total_cost
    total_pnl_pct = (total_pnl_abs / total_cost * 100.0) if total_cost > 0 else 0.0
    day_pnl_sum = float(df["day_pnl_usd"].sum())
    prior_equity = total_mv - day_pnl_sum
    day_pf_pct = (day_pnl_sum / prior_equity * 100.0) if prior_equity > 0 else 0.0
    account_total = total_mv + cash_usd

    try:
        from tasks.buy_zone_alerts import sync_held_discipline_alerts

        sync_held_discipline_alerts(positions, qmap, account_equity=account_total, cooldown_hours=24)
    except Exception:
        pass

    df["role"] = df["ticker"].map(_role_of)
    df["weight"] = (df["market_value_usd"] / total_mv) if total_mv > 0 else 0.0
    role_weight = (
        df.groupby("role", as_index=True)["market_value_usd"].sum() / total_mv if total_mv > 0 else pd.Series(dtype=float)
    )
    role_weight_map = {str(k): float(v) for k, v in role_weight.items()}
    cash_ratio = (cash_usd / account_total) if account_total > 0 else 0.0
    tech_mask = df["role"].eq("高质量成长")
    tech_weight = float(df.loc[tech_mask, "market_value_usd"].sum() / total_mv) if total_mv > 0 else 0.0
    health_text, health_flags = _calc_health(
        df, total_mv=total_mv, cash_ratio=cash_ratio, role_weight=role_weight_map, tech_weight=tech_weight
    )

    mode = st.segmented_control("视图模式", ["简洁模式", "体检模式", "明细模式"], default="简洁模式")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("总资产", f"${account_total:,.2f}", help="现金 + 股票市值")
    m2.metric("现金", f"${cash_usd:,.2f}", delta=f"{cash_ratio*100:.1f}%")
    m3.metric("股票市值", f"${total_mv:,.2f}", delta=f"{(1-cash_ratio)*100:.1f}%")
    m4.metric("今日盈亏", f"${day_pnl_sum:+,.2f}", delta=f"{day_pf_pct:+.2f}%")
    m5.metric("总收益", f"${total_pnl_abs:+,.2f}", delta=f"{total_pnl_pct:+.2f}%")
    m6.metric("组合健康", health_text)
    st.caption(f"持仓标的：{len(df)} 只")
    _render_discipline_engine(df, account_total=account_total)

    if mode == "简洁模式":
        _render_compact_rows(df.sort_values("market_value_usd", ascending=False))

        st.markdown("#### 今日异常")
        gainer = df.loc[df["day_chg_pct"].idxmax()] if not df.empty else None
        loser = df.loc[df["day_chg_pct"].idxmin()] if not df.empty else None
        contrib = df.loc[df["day_pnl_usd"].idxmax()] if not df.empty else None
        drag = df.loc[df["day_pnl_usd"].idxmin()] if not df.empty else None
        near_cost = df[(df["total_return_pct"].abs() <= 2.0)].sort_values("total_return_pct").head(3)
        c1, c2, c3, c4 = st.columns(4)
        if gainer is not None:
            c1.info(f"涨幅最大：{gainer['ticker']} {float(gainer['day_chg_pct']):+.2f}%")
            c2.info(f"跌幅最大：{loser['ticker']} {float(loser['day_chg_pct']):+.2f}%")
            c3.info(f"贡献最大：{contrib['ticker']} ${float(contrib['day_pnl_usd']):+,.2f}")
            c4.info(f"拖累最大：{drag['ticker']} ${float(drag['day_pnl_usd']):+,.2f}")
        if near_cost.empty:
            st.caption("接近成本价：暂无（阈值 ±2%）。")
        else:
            st.caption("接近成本价：" + " · ".join([f"{r.ticker}({r.total_return_pct:+.1f}%)" for r in near_cost.itertuples()]))

    elif mode == "体检模式":
        st.markdown("#### 组合结构体检")
        role_df = pd.DataFrame(
            [{"角色": k, "权重%": round(v * 100, 2)} for k, v in sorted(role_weight_map.items(), key=lambda x: -x[1])]
        )
        st.dataframe(role_df, hide_index=True, use_container_width=True)
        if health_flags:
            st.warning("健康提示：" + "；".join(health_flags))
        else:
            st.success("当前结构没有触发主要风险阈值。")

        st.markdown("#### 操作纪律")
        st.caption(
            "AlphaWatch 不鼓励频繁高抛低吸。默认策略是长期持有高质量资产，用新资金调整结构；"
            "只有触发纪律条件时，才建议减仓或重新评估。"
        )
        rules_hit: list[str] = []
        max_pos = float(df["weight"].max()) if not df.empty else 0.0
        sat_w = role_weight_map.get("高波动卫星", 0.0)
        if max_pos > 0.20:
            rules_hit.append("单只仓位 > 20%，触发强提醒")
        elif max_pos > 0.15:
            rules_hit.append("单只仓位 > 15%，建议评估再平衡")
        if sat_w > 0.10:
            rules_hit.append("高波动卫星 > 10%，建议降至上限内")
        if day_pf_pct <= -2.0:
            rules_hit.append("组合单日跌幅超过 2%，建议降低激进仓位")
        if rules_hit:
            for hit in rules_hit:
                st.write(f"- {hit}")
        else:
            st.write("- 当前未触发强制纪律条件。")

        st.markdown("#### 新资金建议")
        ideas: list[str] = []
        if tech_weight > 0.60:
            ideas.append("科技超配：新钱暂缓继续加科技。")
        if role_weight_map.get("核心底仓", 0.0) < 0.25:
            ideas.append("核心 ETF 偏低：优先补 `VOO/VTI/QQQM`。")
        if role_weight_map.get("防御稳定", 0.0) < 0.10:
            ideas.append("防御不足：关注消费防御/医疗/金融支付/公用事业。")
        if cash_ratio > 0.45:
            ideas.append("现金偏高：可分 4-8 周分批投入，不需要一次性买入。")
        elif cash_ratio < 0.15:
            ideas.append("现金偏低：先保留弹药，再考虑加仓。")
        if not ideas:
            ideas.append("结构接近目标，维持定投与纪律观察。")
        for it in ideas:
            st.write(f"- {it}")

        if st.button("写入组合级健康提醒到 alerts"):
            ok = _insert_manual_alert(
                category="portfolio_health",
                symbol=None,
                title="[组合体检] 健康检查结果",
                what=f"flags={';'.join(health_flags) or 'none'}; cash_ratio={cash_ratio:.3f}; tech_weight={tech_weight:.3f}",
                why="用于跟踪组合结构变化，避免长期偏离目标配置。",
                risk="提醒不等于必须交易，优先用新资金调结构。",
            )
            if ok:
                st.success("已写入 alerts。")
            else:
                st.error("写入失败。")

    else:
        st.subheader("持仓明细（会计视图）")
        disp = df.rename(
            columns={
                "ticker": "标的",
                "qty": "股数",
                "current_px": "现价",
                "market_value_usd": "市值 USD",
                "day_chg_pct": "今日 %",
                "day_pnl_usd": "今日盈亏 $",
                "cost_basis_usd": "成本合计",
                "avg_cost_per_share": "每股成本",
                "total_return_pct": "总收益 %",
            }
        )

        def _colorize(v: object) -> str:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return ""
            if x > 0:
                return "color: #16a34a;"
            if x < 0:
                return "color: #dc2626;"
            return ""

        sty = disp.style
        cols_color = ["今日 %", "今日盈亏 $", "总收益 %"]
        if hasattr(sty, "map"):
            sty = sty.map(_colorize, subset=cols_color)
        else:
            sty = sty.applymap(_colorize, subset=cols_color)

        styled = sty.format(
            {
                "现价": "{:,.4f}",
                "市值 USD": "${:,.2f}",
                "今日 %": "{:+.2f}%",
                "今日盈亏 $": "${:+,.2f}",
                "成本合计": "${:,.2f}",
                "每股成本": "${:,.2f}",
                "总收益 %": "{:+.2f}%",
            },
            na_rep="—",
        )
        st.dataframe(styled, hide_index=True, use_container_width=True)

    pie_c, line_c = st.columns(2)
    with pie_c:
        st.subheader("行业分布（yfinance）")
        with st.spinner("拉取板块信息（有 24h 缓存）…"):
            sec_rows = []
            for sym, mv in zip(df["ticker"], df["market_value_usd"]):
                sec_rows.append({"sector": sector_for_symbol(sym), "mv": float(mv)})
        sdf = pd.DataFrame(sec_rows)
        agg = sdf.groupby("sector", as_index=False)["mv"].sum()
        if agg.empty or agg["mv"].sum() <= 0:
            st.info("暂无行业数据。")
        else:
            fig = px.pie(agg, names="sector", values="mv", hole=0.38)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)

    with line_c:
        st.subheader("近 30 交易日组合净值（Σ 持仓×收盘）")
        with st.spinner("拉取历史收盘价…"):
            curve = equity_curve_daily({k: float(qty_by[k]) for k in symbols})
        if curve.empty:
            st.warning("无法画出净值曲线（离线或 yfinance 无数据）。")
        else:
            cdf = curve.rename("portfolio_usd").to_frame()
            st.line_chart(cdf)

    st.subheader("市场状态")
    macro = snapshot_macro()
    cols = st.columns(5)
    cols[0].metric("VIX", f"{macro.vix:.1f}")
    cols[1].metric("10Y", f"{macro.ten_year_yield_pct:.2f}%")
    cols[2].metric("DXY", f"{macro.dxy:.1f}")
    cols[3].metric("SPY", f"{macro.spy_chg_pct:+.2f}%")
    cols[4].metric("QQQ", f"{macro.qqq_chg_pct:+.2f}%")
