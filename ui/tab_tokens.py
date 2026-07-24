from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

from db.client import get_conn


def render() -> None:
    st.caption(
        "统计：`llm_calls` 记录每次路由调用；`token_usage` 记录 agent 等场景的粗估 token 与是否解析成功 JSON。"
    )
    conn = get_conn(read_only=True)
    usage = pd.DataFrame()
    try:
        rows = pd.read_sql_query(
            """SELECT provider, model, COUNT(*) AS calls FROM llm_calls
               GROUP BY provider, model""",
            conn,
        )
        by_hour = pd.read_sql_query(
            """SELECT strftime('%H', created_at) AS hour_bucket, COUNT(*) AS calls FROM llm_calls
               GROUP BY hour_bucket ORDER BY hour_bucket""",
            conn,
        )
        try:
            usage = pd.read_sql_query(
                """SELECT datetime(created_at) AS t, feature, agent_id, model,
                          input_tokens, output_tokens, success, fallback_used, reason_for_call
                   FROM token_usage ORDER BY id DESC LIMIT 160""",
                conn,
            )
        except Exception:
            usage = pd.DataFrame()
    finally:
        conn.close()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("按供应商")
        st.dataframe(rows, hide_index=True, use_container_width=True)
    with col2:
        st.subheader("按小时（UTC）")
        if by_hour.empty:
            st.info("暂无记录。")
        else:
            st.bar_chart(by_hour.set_index("hour_bucket"))

    st.subheader("token_usage（含 agent 决策）")
    if usage.empty:
        st.info("暂无 `token_usage`；带 agent 跑一轮市场脉冲后会在此出现。")
    else:
        st.dataframe(usage, hide_index=True, use_container_width=True)

    caps = {
        "gemini-lite": ("Gemini Flash-Lite（示意上限）", 1000),
        "gemini-flash": ("Gemini Flash（示意上限）", 250),
        "groq": ("Groq 70B（示意上限）", 14400),
    }
    tally: dict[str, int] = defaultdict(int)
    for _, r in rows.iterrows():
        key = None
        if str(r["provider"]).startswith("gemini-lite"):
            key = "gemini-lite"
        elif str(r["provider"]).startswith("gemini-flash"):
            key = "gemini-flash"
        elif str(r["provider"]) == "groq":
            key = "groq"
        if key:
            tally[key] += int(r["calls"])

    st.subheader("额度条（相对参考上限，非官方计量）")
    for k, (label, cap) in caps.items():
        used = tally.get(k, 0)
        pct = min(100, int(used / cap * 100)) if cap else 0
        st.progress(pct / 100.0, text=f"{label}: {used}/{cap} ({pct}%)")

    ollama_calls = int(rows.loc[rows["provider"] == "ollama", "calls"].sum()) if not rows.empty else 0
    st.caption(f"本地 Ollama 累计调用（含路由兜底）: {ollama_calls} 次")

    with st.expander("试跑 TokenGuard（计数写入 llm_calls）"):
        probe = st.text_input("prompt", value="一句话说明你准备用哪个模型。", key="tg_probe_prompt")
        if st.button("执行路由调用", key="tg_run"):
            from llm.router import TokenGuard

            out = TokenGuard().call("streamlit_manual", probe)
            st.markdown(f"**routed** `{out['routed']}` · **model** `{out['model']}`")
            st.text_area("response", value=out["text"], height=220)
