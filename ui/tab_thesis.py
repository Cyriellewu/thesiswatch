"""📓 投资论点 (Thesis Ledger) tab.

Edit a structured thesis per stock (claims / catalysts / risks / invalidation
conditions / evidence), see calibrated confidence (level + coverage + freshness,
never a bare number), and the Thesis Delta — what changed since you last saved.
"""
from __future__ import annotations

import streamlit as st

from tasks import thesis_store as store
from tasks.thesis import Evidence, InvestmentThesis
from tasks.thesis_delta import diff_thesis
from tasks.thesis_explain import classify_evidence, factor_delta
from data_layer.universe import load_watchlist_tickers

_LEVEL_EMOJI = {"high": "🟢", "medium": "🟡", "low": "🟠", "unknown": "⚪"}
_DIR_EMOJI = {"strengthened": "📈", "weakened": "📉", "no_material_change": "➖"}


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def render() -> None:
    st.markdown("### 📓 投资论点 (Thesis Ledger)")
    st.caption(
        "记录你为什么持有每只票 —— 逻辑、催化剂、风险、失效条件、证据。"
        "系统据此算出可校准的信心（含覆盖度/新鲜度），并追踪较上次「变了什么」。数据只存本机。"
    )

    holdings = load_watchlist_tickers()
    existing = store.all_tickers()
    options = sorted(set(holdings) | set(existing)) or ["AAPL"]
    tk = st.selectbox("选择股票", options, key="thesis_ticker")

    cur = store.load_thesis(tk) or InvestmentThesis(ticker=tk)

    # ---- Thesis Delta banner (vs previous save) ----
    prev = store.load_previous(tk)
    if prev is not None:
        d = diff_thesis(prev, cur)
        st.markdown(f"#### {_DIR_EMOJI.get(d.direction,'➖')} 较上次变化")
        st.info(d.summary_zh)

        # ---- Why Changed: factor-level attribution (71 → 78 because ...) ----
        prev_factors = [{"label": e.text.split("：")[0], "detail": e.text, "delta": (e.weight if e.stance == "support" else -e.weight)} for e in prev.evidence]
        cur_factors = [{"label": e.text.split("：")[0], "detail": e.text, "delta": (e.weight if e.stance == "support" else -e.weight)} for e in cur.evidence]
        fd = factor_delta(prev_factors, cur_factors)
        if fd["movers"]:
            with st.expander("🔍 为什么变了（逐项拆解）", expanded=True):
                st.markdown(f"**信心 {d.confidence_before:.0f} → {d.confidence_after:.0f}**")
                for m in fd["movers"][:8]:
                    arrow = "＋" if m["move"] >= 0 else "－"
                    tag = {"new": "🆕", "removed": "❌", "changed": "🔁"}.get(m["kind"], "")
                    st.markdown(f"- {tag} {m['label']} `{arrow}{abs(m['move']):.0f}`　<small>{m['detail']}</small>", unsafe_allow_html=True)
                st.markdown("**对你意味着什么**")
                st.caption(f"持有周期：{cur.horizon}　·　当前信心：{cur.confidence()['level']}")
                if d.invalidation_triggered:
                    st.warning("⚠️ 触发了你设定的失效条件，建议重新检查逻辑，而不是因短期波动就动作。")
                elif d.direction == "strengthened":
                    st.caption("新证据增强了逻辑，但不代表要立刻加仓；先确认风险是否也变化。")
                elif d.direction == "weakened":
                    st.caption("逻辑走弱，留意是否要降低仓位；但先分清是价格波动还是假设被破坏。")
                else:
                    st.caption("没有 thesis 级变化，通常无需行动。")

    # ---- calibrated confidence ----
    c = cur.confidence()
    m = st.columns(4)
    m[0].metric("信心", f"{_LEVEL_EMOJI.get(c['level'],'⚪')} {c['level']}")
    m[1].metric("信心分", f"{c['score']:.0f}/100")
    m[2].metric("证据覆盖", f"{c['coverage']*100:.0f}%")
    fresh = c["freshness_days"]
    m[3].metric("最新证据", f"{fresh:.0f} 天前" if fresh is not None else "无")
    st.caption(f"支持权重 {c['support_weight']}｜反面权重 {c['counter_weight']}（信心不是单一数字，而是覆盖度×新鲜度×证据平衡的综合）")

    st.divider()

    # ---- editor ----
    st.markdown("#### ✏️ 编辑论点")
    claims = st.text_area("持有逻辑（每行一条）", "\n".join(cur.claims), height=90, key="th_claims")
    catalysts = st.text_area("催化剂（每行一条）", "\n".join(cur.catalysts), height=70, key="th_cat")
    risks = st.text_area("风险（每行一条）", "\n".join(cur.risks), height=70, key="th_risks")
    inval = st.text_area(
        "失效条件（每行一条，用能被信号命中的关键词，如 “Azure 增长连续两季低于 20%”）",
        "\n".join(cur.invalidation_conditions), height=70, key="th_inval")
    horizon = st.text_input("持有周期", cur.horizon, key="th_horizon")

    st.markdown("**证据**（保存后计入信心）")
    with st.expander("➕ 加一条证据", expanded=False):
        ec = st.columns([3, 1, 1, 1])
        ev_text = ec[0].text_input("证据内容", key="th_ev_text")
        ev_kind = ec[1].selectbox("类型", ["price", "news", "fundamental", "smart_money", "macro", "user"], key="th_ev_kind")
        ev_stance = ec[2].selectbox("立场", ["support", "counter"], key="th_ev_stance")
        ev_weight = ec[3].number_input("权重", 0.0, 3.0, 1.0, 0.5, key="th_ev_weight")
        ev_src = st.text_input("来源（URL 或渠道）", key="th_ev_src")
        if st.button("加入证据", key="th_ev_add") and ev_text.strip():
            cur.evidence.append(Evidence(ev_text.strip(), ev_kind, ev_stance, source=ev_src, weight=float(ev_weight)))
            st.session_state["_thesis_pending_ev"] = [e.__dict__ for e in cur.evidence]
            st.success("已加入（记得点下方保存）。")

    if cur.evidence:
        st.markdown("##### 📑 证据（分类 · 新鲜度 · 冲突检测）")
        ev = classify_evidence(cur)
        if ev["conflict"]:
            st.warning(f"⚠️ 证据存在分歧：{ev['support_sources']} 条支持 · {ev['counter_sources']} 条反面")

        def _block(title: str, items: list, note: str) -> None:
            if not items:
                return
            st.markdown(f"**{title}**　<small>{note}</small>", unsafe_allow_html=True)
            for it in items:
                src = f" · [来源]({it['source']})" if it["source"] and not it["auto"] else (f" · {it['source']}" if it["auto"] else "")
                st.markdown(f"- {it['text']}　<small>`{it['kind']}` · {it['freshness']}{src}</small>", unsafe_allow_html=True)

        _block("✅ 事实（观测数据）", ev["facts"], "价格/基本面/宏观等可测数据")
        _block("🧠 模型解读（需判断）", ev["interpretations"], "新闻/大佬持仓等，含解释成分")
        _block("💭 我的假设", ev["assumptions"], "你自己的判断，非外部数据")
        _block("⚔️ 反方观点", ev["counterarguments"], "反对/风险证据——不折叠隐藏")

    if st.button("💾 保存论点", type="primary"):
        new = InvestmentThesis(
            ticker=tk,
            claims=_lines(claims),
            catalysts=_lines(catalysts),
            risks=_lines(risks),
            invalidation_conditions=_lines(inval),
            evidence=cur.evidence,
            horizon=horizon or "12-24 months",
        )
        store.save_thesis(new)
        st.session_state.pop("_thesis_pending_ev", None)
        st.success("已保存。下次修改后回到此页即可看到「变了什么」。")
        st.rerun()

    # ---- Decision history (accountability) ----
    try:
        from tasks import decision_history as dh  # noqa: PLC0415

        hist = dh.history(tk)
        if hist:
            st.divider()
            st.markdown("#### 🕓 决策历史（可复盘）")
            acc = dh.accuracy_summary(hist)
            if acc.get("scored"):
                st.caption(f"已评估 {acc['scored']} 次决策，方向吻合率约 {acc.get('agreement_pct','—')}%（描述性，非收益保证）。")
            else:
                st.caption(acc.get("note", ""))
            for e in reversed(hist[-8:]):
                when = str(e.get("at", ""))[:10]
                emoji = {"no_action": "🟢", "watch": "🟡", "re_evaluate": "🟠"}.get(e.get("status"), "⚪")
                out = ""
                if e.get("outcome_pct") is not None:
                    out = f" → 之后 {e['outcome_pct']:+.1f}%"
                st.markdown(f"- {when} {emoji} 信心 {e.get('confidence','—')}{out}　<small>{'；'.join(e.get('reasons',[])[:2])}</small>", unsafe_allow_html=True)
    except Exception:
        pass

    st.caption("以上为你自己的投资记录，不构成投资建议。")
