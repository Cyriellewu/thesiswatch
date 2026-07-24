"""📐 组合风险 (Portfolio) tab — hidden exposures + what-if simulator.

Answers "my portfolio looks diversified, but what common risks am I betting on?"
Shows weighted theme exposures (AI infra / semis / mega-cap / rate / China) and
lets the user simulate add/reduce trades to see how concentration + exposures
shift. Decision-support only — no auto-trading.
"""
from __future__ import annotations

import streamlit as st

from tasks import exposure
from tasks import willow_agent

_LEVEL_EMOJI = {"高": "🔴", "中": "🟡", "低": "🟢"}


@st.cache_data(ttl=300, show_spinner=False)
def _advice_cached() -> dict:
    return willow_agent.build_advice()


def render() -> None:
    st.markdown("### 📐 组合风险")
    st.caption(
        "表面上分散，实际可能都押在同一批共同风险上。这里把持仓映射到主题因子（AI 基础设施 / 半导体 / 大型科技 / 利率 / 地缘），"
        "并让你模拟加减仓看曝险怎么变。仅决策模拟，不下单。"
    )
    advice = _advice_cached()
    positions = [
        {"ticker": s["ticker"], "weight_pct": float(s.get("weight_pct") or 0.0), "mv": float(s.get("mv") or 0.0)}
        for s in advice.get("stocks", []) if (s.get("weight_pct") or 0) > 0
    ]
    if not positions:
        st.info("还没有持仓数据。到 ✏️ 编辑持仓 填入你的组合。")
        return

    exps = exposure.compute_exposures(positions)

    st.markdown("#### 🎯 隐藏曝险")
    if not exps:
        st.caption("暂无已知主题映射（可能持仓不在主题表内）。")
    for e in exps:
        emoji = _LEVEL_EMOJI.get(e.level, "🟢")
        with st.container(border=True):
            st.markdown(f"**{emoji} {e.theme}** · {e.pct:.0f}%（{e.level}）")
            st.progress(min(1.0, e.pct / 100.0))
            with st.expander("哪些持仓贡献了这个曝险"):
                for c in e.contributors:
                    st.caption(f"{c['ticker']}：仓位 {c['weight_pct']:.0f}% → 贡献 {c['contribution']:.0f}%")

    st.divider()

    # ---- What-if simulator ----
    st.markdown("#### 🔮 What-if 模拟")
    st.caption("加/减某只票，看集中度和曝险怎么变。只模拟，不下单。")
    tickers = [p["ticker"] for p in positions]
    cash = float(advice.get("portfolio", {}).get("cash") or 0.0)

    changes = []
    with st.form("whatif"):
        n = st.number_input("模拟几笔调整", 1, 4, 1)
        for i in range(int(n)):
            c = st.columns([2, 2])
            tk = c[0].selectbox(f"标的 #{i+1}", tickers, key=f"wi_tk_{i}")
            amt = c[1].number_input(f"金额$（+加 / -减）#{i+1}", value=0.0, step=100.0, key=f"wi_amt_{i}")
            if amt:
                changes.append({"ticker": tk, "delta_usd": amt})
        go = st.form_submit_button("模拟", use_container_width=True)

    if go and changes:
        res = exposure.simulate(positions, cash, changes)
        m = st.columns(2)
        m[0].metric("最大单票权重", f"{res['after_top_weight']:.0f}%", f"{res['after_top_weight']-res['before_top_weight']:+.0f}%")
        m[1].metric("现金占比", f"{res['after_cash_pct']:.0f}%")
        st.markdown("**曝险变化**")
        before = {e["theme"]: e["pct"] for e in res["before_exposures"]}
        after = {e["theme"]: e["pct"] for e in res["after_exposures"]}
        for theme in sorted(set(before) | set(after), key=lambda t: after.get(t, 0), reverse=True):
            b, a = before.get(theme, 0.0), after.get(theme, 0.0)
            if abs(a - b) >= 0.5:
                st.caption(f"{theme}：{b:.0f}% → {a:.0f}%（{a-b:+.0f}%）")
            else:
                st.caption(f"{theme}：{a:.0f}%（不变）")

    st.caption("以上为组合分析，不构成投资建议。主题映射是透明可编辑的规则表，不是模型猜测。")
