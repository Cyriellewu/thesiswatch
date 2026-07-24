"""🏛️ 投资委员会 tab — multi-persona debate over one stock or the whole portfolio.

Renders the pure-engine committee verdict with an st.status thought-chain, a
verdict banner, persona cards (with 规则/模型润色 provenance badges), and the
chair's synthesis. Works fully offline (rule badges only).
"""
from __future__ import annotations

import streamlit as st

from tasks import committee as cm
from tasks import willow_agent

_STANCE_EMOJI = {"bullish": "🟢", "neutral": "⚪", "bearish": "🔴", "veto": "🛑"}


@st.cache_data(ttl=300, show_spinner=False)
def _advice_cached() -> dict:
    return willow_agent.build_advice()


def render() -> None:
    st.markdown("### 🏛️ 投资委员会")
    st.caption(
        "价值派 / 动量派 / 风控官 / 大佬跟随 各出观点,主持人综合成一个结论。"
        "全程基于已算好的事实,LLM 只做可选润色、不编造数字。风控官有一票否决权。"
    )
    advice = _advice_cached()
    holdings = [s["ticker"] for s in advice.get("stocks", [])]

    row = st.columns([2, 2, 1])
    with row[0]:
        subject = st.selectbox("开会主题", ["📊 整个组合"] + holdings, key="committee_subject")
    with row[1]:
        use_llm = st.toggle("用大模型润色（需 key，否则纯规则）", value=False, key="committee_llm")
    with row[2]:
        run = st.button("🏛️ 开会", use_container_width=True)

    gurus = st.session_state.get("gurus_loaded")

    if run:
        with st.status("投资委员会开会中…", expanded=True) as s:
            st.write("📥 汇总事实（信号 / 信念因子 / 大佬重叠 / 集中度）…")
            if subject == "📊 整个组合":
                verdict = cm.run_committee_for_portfolio(advice, gurus=gurus, use_llm=use_llm)
            else:
                verdict = cm.run_committee_for_stock(advice, subject, gurus=gurus, use_llm=use_llm)
            for v in verdict.views:
                st.write(f"🗣 {v.name_zh} 发言（{v.score:.0f} 分）…")
            st.write("🧑‍⚖️ 主持人综合…")
            s.update(label="✅ 委员会已给出结论", state="complete", expanded=False)
        st.session_state["committee_verdict"] = verdict

    verdict = st.session_state.get("committee_verdict")
    if not verdict:
        st.info("选好主题,点「开会」即可。")
        return

    # --- verdict banner ---
    emoji = _STANCE_EMOJI.get(verdict.stance, "⚪")
    st.markdown(f"#### {emoji} 结论：{verdict.call_zh}")
    bc = st.columns([1, 3])
    bc[0].metric("信心", f"{verdict.confidence}/100")
    with bc[1]:
        for w in verdict.warnings:
            st.warning(w)
    st.markdown(f"**主持人综合：** {verdict.rationale_zh}")
    st.caption(verdict.dissent_zh)
    badge = "模型润色" if verdict.source == "llm" else "规则"
    st.caption(f"结论来源：{badge}")

    st.divider()

    # --- persona cards ---
    cols = st.columns(2)
    for i, v in enumerate(verdict.views):
        with cols[i % 2]:
            with st.container(border=True):
                pe = _STANCE_EMOJI.get(v.stance, "⚪")
                pbadge = "模型润色" if v.source == "llm" else "规则"
                st.markdown(f"**{pe} {v.name_zh}** · {v.score:.0f} 分　`{pbadge}`")
                st.markdown(v.prose_zh)
                with st.expander("依据"):
                    for b in v.bullets:
                        st.markdown(f"- {b}")

    st.caption("以上为规则化提示,不构成投资建议;请在券商 App 自行判断下单。")
