from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from db.client import get_conn

_ROOT = Path(__file__).resolve().parents[1]
_LEVEL_FILTER = {"全部": None, "紧急": "urgent", "重要": "major", "关注": "attention", "日常": "routine"}


def render() -> None:
    level_name = st.selectbox("严重程度（alerts 表）", list(_LEVEL_FILTER.keys()), index=0)
    show_demo = st.checkbox("显示旧 demo 新闻 alerts", value=False)
    show_legacy = st.checkbox("同时显示旧脚手架里的 `push_events` 演示行", value=False)

    conn = get_conn(read_only=True)
    try:
        level = _LEVEL_FILTER[level_name]
        where_parts = []
        params: list[str] = []
        if level:
            where_parts.append("level = ?")
            params.append(level)
        if not show_demo:
            where_parts.append(
                "LOWER(COALESCE(title,'') || ' ' || COALESCE(what_happened,'')) NOT LIKE '%demo headline%'"
            )
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        if level:
            alerts_df = pd.read_sql_query(
                f"""SELECT datetime(occurred_at) AS t, level, category, COALESCE(symbol,'') AS symbol,
                          title, what_happened, why_matters, risk, pushed_ntfy AS ntfy
                   FROM alerts
                   {where_sql}
                   ORDER BY occurred_at DESC LIMIT 200""",
                conn,
                params=params,
            )
        else:
            alerts_df = pd.read_sql_query(
                f"""SELECT datetime(occurred_at) AS t, level, category, COALESCE(symbol,'') AS symbol,
                          title, what_happened, why_matters, risk, pushed_ntfy AS ntfy
                   FROM alerts
                   {where_sql}
                   ORDER BY occurred_at DESC LIMIT 200""",
                conn,
                params=params,
            )
        legacy_df = (
            pd.read_sql_query(
                """SELECT datetime(occurred_at) AS t, level, category,
                          COALESCE(related_symbol,'') AS symbol,
                          title, summary AS what_happened, body AS why_matters,
                          '' AS risk, 0 AS ntfy
                   FROM push_events ORDER BY occurred_at DESC LIMIT 120""",
                conn,
            )
            if show_legacy
            else pd.DataFrame()
        )
        pending_alerts = pd.read_sql_query(
            "SELECT COUNT(*) AS n FROM alerts WHERE pushed_ntfy = 0",
            conn,
        )
        latest_pulse = pd.read_sql_query(
            """SELECT MAX(created_at) AS t FROM market_signals""",
            conn,
        )
        recent_signals = pd.read_sql_query(
            """SELECT datetime(created_at) AS t, COALESCE(symbol,'') AS symbol, signal_type, score
               FROM market_signals
               ORDER BY id DESC LIMIT 10""",
            conn,
        )
        recent_decisions = pd.read_sql_query(
            """SELECT datetime(created_at) AS t, account_id, action, COALESCE(symbol,'') AS symbol,
                      ROUND(COALESCE(confidence,0), 2) AS confidence
               FROM agent_decisions
               ORDER BY id DESC LIMIT 10""",
            conn,
        )
        recent_eval = pd.read_sql_query(
            """SELECT datetime(evaluated_at) AS t, eval_horizon, outcome,
                      ROUND(return_pct, 2) AS return_pct, decision_id
               FROM decision_outcomes
               ORDER BY id DESC LIMIT 10""",
            conn,
        )
        latest_eval = pd.read_sql_query(
            "SELECT MAX(evaluated_at) AS t FROM decision_outcomes",
            conn,
        )
    finally:
        conn.close()

    st.subheader("统一信号推送（SQLite `alerts`，人话字段）")
    pend_n = int(pending_alerts["n"].iloc[0]) if not pending_alerts.empty else 0
    pulse_v = latest_pulse["t"].iloc[0] if not latest_pulse.empty else None
    pulse_t = "" if pd.isna(pulse_v) else str(pulse_v)
    st.caption(f"待发送 alerts：**{pend_n}** 条 · 最近 pulse 信号写入时间：**{pulse_t or '暂无'}**")
    eval_v = latest_eval["t"].iloc[0] if not latest_eval.empty else None
    eval_t = "" if pd.isna(eval_v) else str(eval_v)
    st.caption(f"最近评估任务落库时间：**{eval_t or '暂无'}**")
    if not show_demo:
        st.caption("已默认隐藏旧 demo 新闻 alerts；后续真实新闻不可用时不会再生成 demo 新闻提醒。")
    if alerts_df.empty and legacy_df.empty:
        st.info("还没有任何 `alerts`。点击下方「运行市场脉冲」抓一次行情/新闻并生成记录。")
    elif alerts_df.empty:
        st.warning("当前 `alerts` 为空。")
    else:
        st.dataframe(alerts_df, hide_index=True, use_container_width=True)

    if show_legacy and not legacy_df.empty:
        st.subheader("旧版 `push_events`（仅回顾）")
        st.dataframe(legacy_df, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("运行状态面板（Phase A）")
    c1, c2, c3 = st.columns(3)
    c1.metric("Pending alerts", f"{pend_n}")
    c2.metric("Recent signals(10)", f"{len(recent_signals)}")
    c3.metric("Recent outcomes(10)", f"{len(recent_eval)}")
    a, b, c = st.columns(3)
    with a:
        st.caption("最近信号")
        st.dataframe(recent_signals, hide_index=True, use_container_width=True)
    with b:
        st.caption("最近决策")
        st.dataframe(recent_decisions, hide_index=True, use_container_width=True)
    with c:
        st.caption("最近评估")
        st.dataframe(recent_eval, hide_index=True, use_container_width=True)

    st.divider()
    with st.expander("清理无意义 demo 新闻 alerts", expanded=False):
        st.caption("只删除标题/内容包含 `demo headline` 的历史 alerts，不影响真实价格、宏观、技术信号。")
        if st.button("删除历史 demo 新闻 alerts"):
            w = get_conn(read_only=False)
            try:
                cur = w.execute(
                    """DELETE FROM alerts
                       WHERE LOWER(COALESCE(title,'') || ' ' || COALESCE(what_happened,'')) LIKE '%demo headline%'"""
                )
                deleted = int(cur.rowcount or 0)
                w.commit()
            finally:
                w.close()
            st.success(f"已删除 {deleted} 条 demo 新闻 alerts。")
            st.rerun()

    st.divider()
    st.subheader("运行一次「市场脉冲」（与虚拟账户共用同一抓取）")
    do_push = st.checkbox("完成后推送市场类 ntfy（最多 5 条，按优先级）", value=bool(os.environ.get("NTFY_TOPIC", "").strip()))
    run_agents = st.checkbox(
        "顺带跑虚拟 agent（落库 `agent_decisions`/`agent_trades`；勾 ntfy 时也会推成交摘要）",
        value=True,
    )
    force_agent_review = st.checkbox(
        "强制两条虚拟账户复盘（即使今天已经跑过）",
        value=False,
        disabled=not run_agents,
    )
    if st.button("执行 market pulse", type="primary"):
        from db.client import bootstrap_database
        from jobs.market_pulse import run_market_pulse

        bootstrap_database()
        w = get_conn(read_only=False)
        try:
            stats = run_market_pulse(
                conn=w,
                push_ntfy=do_push,
                ntfy_limit=5,
                run_agents=run_agents,
                force_agent_review=force_agent_review,
            )
            w.commit()
        finally:
            w.close()
        ac = stats.get("agent_cycle") or {}
        st.success(
            f"完成：{stats['quotes']} 报价 · 新新闻入库 {stats['news_rows_new']} · "
            f"信号 {stats['signals']} · 新 alert {stats['alerts_materialized']} · "
            f"机会池重打分 {stats.get('opportunity_rescored', 0)} 条 · "
            f"权益快照 {stats['equity_snapshots']} · 市场类 ntfy {stats['ntfy_pushed']}"
            + (
                f" · agent 决策 {ac.get('decisions', 0)} · 模拟成交腿数 {ac.get('trade_legs', 0)} · 成交 ntfy {ac.get('ntfy', 0)}"
                if run_agents
                else ""
            )
        )
        sb = stats.get("signal_breakdown") or {}
        if sb:
            st.caption(
                "signals breakdown: "
                f"price={sb.get('price', 0)}, news={sb.get('news', 0)}, "
                f"technical={sb.get('technical', 0)}, dynamic={sb.get('dynamic', 0)}, "
                f"bundle={sb.get('bundles', 0)}, macro={sb.get('macro', 0)}"
            )
        st.rerun()

    if st.button("执行 1d 决策评估（jobs/evaluate_decisions）"):
        from db.client import bootstrap_database
        from jobs.evaluate_decisions import evaluate_decisions_1d

        bootstrap_database()
        stats = evaluate_decisions_1d()
        st.success(
            f"评估完成：扫描 {stats.scanned} 条 · 新 outcome {stats.inserted} 条 · "
            f"无成交跳过 {stats.skipped_no_trade} 条 · 更新信号有效性 {stats.updated_effectiveness} 次"
        )
        st.rerun()

    st.divider()
    st.subheader("ntfy 推送")
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER_URL", "https://ntfy.sh").strip()
    if topic:
        st.caption(f"当前：`NTFY_SERVER_URL={server}` · `NTFY_TOPIC={topic}`")
    else:
        st.warning("未检测到 `NTFY_TOPIC`。请在 `.env` 填写你订阅的主题名。")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("发送连通性测试", disabled=not topic):
            from push.notify import ping

            if ping():
                st.success("已发到 ntfy。")
            else:
                st.error("发送失败 — 请看运行 Streamlit 的终端日志。")
    with col_b:
        from jobs.scheduler import daily_refresh
        from data_layer.universe import pulse_symbol_universe
        from push.notify import send_morning_digest

        if st.button(
            "早间文字简报（独立于 alerts）",
            help="仍使用 `daily_refresh`，不会写入 alerts 表",
        ):
            payload = daily_refresh(pulse_symbol_universe())
            if send_morning_digest(payload):
                st.success("简报已发送。")
            else:
                st.error("简报发送失败（或未配置 ntfy topic）。")

    st.divider()
    st.subheader("日内大变动提醒（手动扫描）")
    cfg_path = _ROOT / "config" / "settings.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    icfg = dict(cfg.get("intraday_alerts") or {})
    st.caption(
        "当前配置："
        f"enabled={bool(icfg.get('enabled', True))}, "
        f"pct_change_threshold={icfg.get('pct_change_threshold', 5)}, "
        f"volume_days={icfg.get('volume_days', 5)}, "
        f"volume_multiplier={icfg.get('volume_multiplier', 2)}"
    )
    if st.button("手动执行一次日内扫描"):
        from tasks.intraday_alerts import run_intraday_alerts_once

        out = run_intraday_alerts_once(cfg)
        st.success(f"扫描 {out.get('scanned', 0)} 只，触发 {out.get('triggered', 0)} 条提醒。")
