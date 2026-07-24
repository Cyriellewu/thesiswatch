from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

from data_layer.market_data import fetch_quotes
from data_layer.persist import persist_quotes
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from db.client import bootstrap_database, get_conn, repo_root
from agents.runner import run_all_agents
from data.virtual_accounts import load_virtual_account
from tasks.virtual_accounts import (
    compute_account_stats,
    init_agent_5k_account,
    init_mirror_account_from_real_positions,
    run_agent_for_account,
)

from agents.goal_profiles import (
    dashboard_goal_line_zh,
    dumps_agent_goal,
    horizons_for_selector,
    objectives_for_selector,
    parse_agent_goal,
    risk_modes_for_selector,
    yaml_goal_defaults,
)

NY = ZoneInfo("America/New_York")

# 核心三张卡片（其余账户收进「高级」折叠）
_CORE_ACCOUNTS: list[str] = ["buyhold", "takeover-balanced", "fresh-balanced"]

_CARD_TITLES: dict[str, str] = {
    "buyhold": "My Portfolio · Buy & Hold",
    "takeover-balanced": "Agent Takeover · My Portfolio",
    "fresh-balanced": "Fresh $5,000 · Balanced",
}


def _json_id_list(blob: object) -> list[int]:
    if blob is None or (isinstance(blob, float) and pd.isna(blob)):
        return []
    if isinstance(blob, list):
        raw = blob
    else:
        try:
            s = str(blob).strip()
            if not s:
                return []
            raw = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out[:12]


def _format_decision_sources(conn: object, ser: pd.Series) -> str:
    alert_ids = _json_id_list(ser.get("source_alert_ids"))
    sig_ids = _json_id_list(ser.get("source_signal_ids"))
    parts: list[str] = []
    if alert_ids:
        ph = ",".join(["?"] * len(alert_ids))
        rows = conn.execute(
            f"SELECT id, title FROM alerts WHERE id IN ({ph}) ORDER BY id DESC",
            tuple(alert_ids),
        ).fetchall()
        for r in rows:
            parts.append(f"提醒{r['id']}: {str(r['title'])[:92]}")
    if sig_ids:
        ph = ",".join(["?"] * len(sig_ids))
        rows = conn.execute(
            f"SELECT id, signal_type, COALESCE(symbol,'') AS sym, score FROM market_signals WHERE id IN ({ph}) ORDER BY id DESC",
            tuple(sig_ids),
        ).fetchall()
        for r in rows:
            parts.append(f"信号{r['id']} {r['signal_type']} {r['sym']} @{r['score']}")
    return " · ".join(parts[:10]) if parts else ""


def _ny_today() -> str:
    return datetime.now(NY).date().isoformat()


def _latest_decision_by_account(conn: object) -> dict[str, pd.Series]:
    df = pd.read_sql_query(
        """SELECT * FROM agent_decisions ORDER BY id DESC""",
        conn,
    )
    if df.empty:
        return {}
    picked: dict[str, pd.Series] = {}
    for _, row in df.iterrows():
        aid = str(row["account_id"])
        if aid not in picked:
            picked[aid] = row
    return picked


def _max_activity_ts(conn: object, aid: str) -> str | None:
    ts_vals: list[str] = []
    for sql in (
        "SELECT MAX(ts) AS mx FROM agent_decisions WHERE account_id = ?",
        "SELECT MAX(ts) AS mx FROM agent_equity_snapshots WHERE account_id = ?",
        "SELECT MAX(ts) AS mx FROM agent_trades WHERE account_id = ?",
    ):
        r = conn.execute(sql, (aid,)).fetchone()
        mx = r["mx"] if r is not None else None
        if mx:
            ts_vals.append(str(mx))
    return max(ts_vals) if ts_vals else None


def _daemon_schedule_hint() -> str:
    cfg_path = repo_root() / "config" / "settings.yaml"
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    d = raw.get("daemon") or {}
    o = str(d.get("open_et") or "10:00").strip()
    c = str(d.get("close_et") or "15:30").strip()
    return (
        f"`python -m jobs.daemon` 在 **NYSE 交易日**约 **{o} ET** 与 **{c} ET** 各跑一轮 market pulse + 虚拟 agent（见 `config/settings.yaml`）。"
        "本页（Streamlit）**不会**自动每隔几秒刷新；只有你点「市场脉冲」等按钮、或后台 daemon 跑完，数据才会更新。"
    )


def _market_quote_health() -> tuple[int, str]:
    conn = get_conn(read_only=True)
    try:
        r = conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(MAX(retrieved_at), '') AS mx
            FROM market_quotes
            WHERE datetime(retrieved_at) >= datetime('now','-2 hours')
            """
        ).fetchone()
        n = int(r["n"] if r else 0)
        mx = str(r["mx"] if r else "")
        return n, (mx or "—")
    finally:
        conn.close()


def _latest_agent_failure() -> tuple[str, str, str]:
    conn = get_conn(read_only=True)
    try:
        row = conn.execute(
            """
            SELECT account_id, COALESCE(error,'') AS err, COALESCE(ts,'') AS ts
            FROM agent_decisions
            WHERE COALESCE(success,1) = 0
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return "—", "—", "—"
        return str(row["account_id"] or "—"), str(row["err"] or "—"), str(row["ts"] or "—")
    finally:
        conn.close()


def _latest_agent_failure_after(latest_ts: str) -> tuple[str, str, str]:
    conn = get_conn(read_only=True)
    try:
        row = conn.execute(
            """
            SELECT account_id, COALESCE(error,'') AS err, COALESCE(ts,'') AS ts
            FROM agent_decisions
            WHERE COALESCE(success,1) = 0
              AND COALESCE(ts,'') >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (latest_ts,),
        ).fetchone()
        if not row:
            return "—", "—", "—"
        return str(row["account_id"] or "—"), str(row["err"] or "—"), str(row["ts"] or "—")
    finally:
        conn.close()


def _latest_agent_run_ts() -> str:
    conn = get_conn(read_only=True)
    try:
        row = conn.execute("SELECT COALESCE(MAX(ts),'') AS mx FROM agent_decisions").fetchone()
        return str(row["mx"] if row else "") or "—"
    finally:
        conn.close()


def _refresh_agent_quotes(conn: object) -> dict[str, float]:
    syms = sorted({*load_watchlist_tickers(), *load_opportunity_tickers()})
    quotes = fetch_quotes(syms)
    persist_quotes(conn, quotes)
    return {q.symbol.upper(): float(q.px) for q in quotes if float(q.px or 0.0) > 0}


def _positions_detail(sub: pd.DataFrame, pxm: dict[str, float]) -> pd.DataFrame:
    rows: list[dict] = []
    for _, pr in sub.iterrows():
        sym = str(pr["symbol"]).upper()
        qty = float(pr["qty"])
        px = float(pxm.get(sym) or 0.0)
        mv = qty * px
        rows.append(
            {
                "symbol": sym,
                "qty": qty,
                "px_live": round(px, 4) if px else None,
                "mv_live": round(mv, 2),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("mv_live", ascending=False)
    return df


def _today_action_line(day_tr: pd.DataFrame, dec: pd.Series | None) -> str:
    if not day_tr.empty:
        parts: list[str] = []
        for _, tr in day_tr.iterrows():
            act = str(tr.get("action") or tr.get("side") or "")
            sy = str(tr.get("symbol") or "")
            try:
                u = float(tr.get("usd") or 0.0)
            except (TypeError, ValueError):
                u = 0.0
            parts.append(f"{act} {sy} (~${abs(u):,.0f})".strip())
        return " · ".join(parts[:8])
    if dec is not None:
        if int(dec.get("success") or 1) == 0:
            return "⚠️ AI 决策失败（本轮无有效动作）"
        a = str(dec.get("action") or "—")
        sy = str(dec.get("symbol") or "")
        st2 = str(dec.get("symbol_to") or "")
        tail = f" {sy}" if sy else ""
        if st2:
            tail += f" → {st2}"
        return f"{a}{tail}".strip()
    return "尚无今日记录（可能还未跑脉冲 / agent cycle）"


def _render_goal_editor(aid: str, goal_cur: dict) -> None:
    with st.expander("调整投资目标（期限 / 收益风格 / 风控）", expanded=False):
        hz_opts = horizons_for_selector()
        hz_codes = [c for _, c in hz_opts]
        hz_labels = dict(hz_opts)
        ob_opts = objectives_for_selector()
        ob_codes = [c for _, c in ob_opts]
        ob_labels = dict(ob_opts)
        rm_opts = risk_modes_for_selector()
        rm_codes = [c for _, c in rm_opts]
        rm_labels = dict(rm_opts)
        try:
            hz_ix = hz_codes.index(goal_cur["horizon"])
        except ValueError:
            hz_ix = 0
        try:
            ob_ix = ob_codes.index(goal_cur["objective"])
        except ValueError:
            ob_ix = 0
        try:
            rm_ix = rm_codes.index(goal_cur["risk_mode"])
        except ValueError:
            rm_ix = 0
        pick_hz = st.selectbox(
            "目标窗口（horizon）",
            options=hz_codes,
            index=hz_ix,
            format_func=lambda c: hz_labels.get(c, c),
            key=f"goal_horizon_{aid}",
        )
        pick_ob = st.selectbox(
            "收益风格（objective）",
            options=ob_codes,
            index=ob_ix,
            format_func=lambda c: ob_labels.get(c, c),
            key=f"goal_obj_{aid}",
        )
        pick_rm = st.selectbox(
            "风控姿态（risk_mode）",
            options=rm_codes,
            index=rm_ix,
            format_func=lambda c: rm_labels.get(c, c),
            key=f"goal_rm_{aid}",
        )
        if st.button("保存投资目标", key=f"goal_save_{aid}"):
            bootstrap_database()
            w = get_conn(read_only=False)
            try:
                ng = {"horizon": pick_hz, "objective": pick_ob, "risk_mode": pick_rm}
                w.execute(
                    """UPDATE agent_accounts SET agent_goal_json = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (dumps_agent_goal(ng), aid),
                )
                w.commit()
                st.success(f"已更新 {aid} 的投资目标。")
                st.rerun()
            finally:
                w.close()


def _render_one_card(
    *,
    conn: object,
    aid: str,
    accounts: pd.DataFrame,
    view: pd.DataFrame,
    pos: pd.DataFrame,
    pxm: dict[str, float],
    dec_map: dict[str, pd.Series],
    source_hints: dict[str, str],
    trades_today: pd.DataFrame,
    tk_map: dict[str, int],
    bh_equity: float | None,
) -> None:
    row = accounts[accounts["id"] == aid].iloc[0] if aid in set(accounts["id"].astype(str)) else None
    if row is None:
        st.warning(f"库里缺少账户 `{aid}`，请先 `bootstrap_database` / 建库。")
        return

    title = _CARD_TITLES.get(aid, str(row["label"]))
    st.markdown(f"### {title}")
    st.caption(f"内部 id：`{aid}` · {str(row.get('label') or '')}")

    vv = view[view["id"] == aid].iloc[0]
    cash = float(vv["cash"])
    eq = float(vv["equity_live"])
    pv = float(vv["positions_mv"])

    cols = st.columns(3)
    cols[0].metric("净值（现价粗算）", f"${eq:,.2f}")
    cols[1].metric("现金", f"${cash:,.2f}")
    cols[2].metric("持仓市值", f"${pv:,.2f}")

    if aid == "takeover-balanced" and bh_equity is not None:
        st.metric("对比 Buy & Hold（同一快照下的现价净值）", f"${eq - bh_equity:+,.2f}")

    goal_cur = parse_agent_goal(row.get("agent_goal_json"))
    st.markdown(f"**投资目标**：{dashboard_goal_line_zh(goal_cur)}")

    if int(row.get("allow_trades") or 0) == 1 and aid != "buyhold":
        _render_goal_editor(aid, goal_cur)
    elif aid == "buyhold":
        st.caption("对照组：镜像你的清单，**永不交易**；用于和 Takeover / Fresh 对比。")

    sub = pos[pos["account_id"] == aid] if not pos.empty else pd.DataFrame()
    ptab = _positions_detail(sub, pxm)
    st.markdown("**当前持仓**")
    if ptab.empty:
        st.write("（无股票仓位，可能全是现金或尚未同步镜像）")
    else:
        st.dataframe(ptab, hide_index=True, use_container_width=True)

    dec = dec_map.get(aid)
    day_tr = trades_today[trades_today["account_id"] == aid] if not trades_today.empty else pd.DataFrame()

    st.markdown("**今天动作**")
    st.write(_today_action_line(day_tr, dec))

    if dec is not None:
        if int(dec.get("success") or 1) == 0:
            st.error(f"⚠️ AI 决策失败 - {str(dec.get('error') or 'unknown error')}")
            st.caption(
                "本轮失败已真实写库（success=0），不会伪装成 HOLD。"
                f" 提供方：{str(dec.get('provider') or '—')} / 模型：{str(dec.get('model') or '—')} / 尝试次数：{int(dec.get('attempts') or 0)}"
            )
            st.divider()
            return
        st.markdown("**为什么（最近一次判断）**")
        st.write(str(dec.get("plain_reason") or "—"))
        st.markdown("**风险**")
        st.write(str(dec.get("plain_risk") or "—"))
        nw = str(dec.get("next_watch") or "").strip()
        if nw:
            st.caption(f"接下来看什么：{nw}")
        llm_tag = "已问模型" if int(dec.get("used_llm") or 0) else "纯规则/未调模型"
        model = dec.get("model") or "—"
        llm_n = tk_map.get(aid, 0)
        sh = source_hints.get(aid, "")
        bits = [llm_tag, f"模型 {model}", f"今天 token 记录约 {llm_n} 条"]
        if sh:
            bits.append(f"来源摘录：{sh}")
        st.caption(" · ".join(bits))
    else:
        st.info("还没有任何 `agent_decisions` 记录。")

    mx = _max_activity_ts(conn, aid)
    st.caption(f"**最近一次相关更新时间**（决策 / 权益快照 / 成交之一）：{mx or '—'}")

    if not day_tr.empty:
        st.caption("**今天成交**")
        st.dataframe(day_tr, hide_index=True, use_container_width=True)
    else:
        st.caption("今天暂无成交。")

    st.divider()


def render() -> None:
    st.caption(
        "虚拟成交写在本地 SQLite：`agent_decisions` / `agent_trades` / `agent_positions`。"
        "不会向券商下单。"
    )
    gem_ok = bool(str(os.environ.get("GEMINI_API_KEY") or "").strip())
    groq_ok = bool(str(os.environ.get("GROQ_API_KEY") or "").strip())
    st.caption(
        f"当前进程密钥检测：GEMINI={'OK' if gem_ok else 'MISSING'} · GROQ={'OK' if groq_ok else 'MISSING'}"
    )
    qn, qts = _market_quote_health()
    st.caption(f"行情可用性：最近2小时报价 {qn} 条 · 最近报价时间 {qts}")
    with st.expander("环境自检", expanded=False):
        a1, a2, a3 = st.columns(3)
        a1.metric("最近 Agent 运行", _latest_agent_run_ts())
        fail_aid, fail_err, fail_ts = _latest_agent_failure()
        a2.metric("最近失败账户", fail_aid)
        a3.metric("最近失败时间", fail_ts)
        st.caption(f"最近失败原因：{fail_err}")
        if not gem_ok and not groq_ok:
            st.error("当前进程未读取到 GEMINI/GROQ 密钥。请在启动 Streamlit/daemon 的同一终端重新加载 .env 后重启进程。")
        elif qn <= 0:
            st.warning("最近 2 小时没有行情报价，可能是行情抓取链路未跑或网络源不可用。")
        else:
            st.success("密钥与行情看起来都可用，可直接点击“立即跑一轮 Agent”。")
    st.info(_daemon_schedule_hint())
    c_run, c_note = st.columns([1, 4])
    with c_run:
        if st.button("立即跑一轮 Agent", use_container_width=True):
            w = get_conn(read_only=False)
            try:
                px_map = _refresh_agent_quotes(w)
                rs = run_all_agents(conn=w, px_map=px_map, force_review=True)
                w.commit()
                ok = sum(1 for r in rs if r.get("ok"))
                fail = sum(1 for r in rs if not r.get("ok"))
                st.success(f"已刷新报价 {len(px_map)} 条并执行：成功 {ok} / 失败 {fail}")
                if fail:
                    errs = [f"{r.get('agent_id')}: {r.get('error', 'unknown')}" for r in rs if not r.get("ok")]
                    st.warning("；".join(errs[:3]))
                st.rerun()
            finally:
                w.close()
    with c_note:
        st.caption("用于手工验证 agent 决策链路（不会下真实券商单）。")

    with st.expander("三件事分别是什么？", expanded=False):
        st.markdown(
            "1. **Buy & Hold**：你的清单镜像，只看「若我不动会怎样」。  \n"
            "2. **Agent Takeover**：同一镜像起跑，但 agent 只能在 SQLite 里模拟买/卖/减仓/换仓。  \n"
            "3. **Fresh \\$5k**：单独一笔约 \\$5k 虚拟现金，从零建仓，可长期 HOLD。  \n"
            "机会雷达、宏观、推送都是为这三块服务，不是另一套独立游戏。"
        )

    conn = get_conn(read_only=True)
    token_totals = pd.DataFrame()
    source_hints: dict[str, str] = {}
    accounts = pd.DataFrame()
    pos = pd.DataFrame()
    curves = pd.DataFrame()
    dec_map: dict[str, pd.Series] = {}
    trades_today = pd.DataFrame()
    tu = pd.DataFrame()
    try:
        accounts = pd.read_sql_query(
            """SELECT id, label, mode, style, cash_usd, allow_trades,
                      COALESCE(agent_goal_json,'') AS agent_goal_json
               FROM agent_accounts ORDER BY mode, id""",
            conn,
        )
        accounts["id"] = accounts["id"].astype(str)
        pos = pd.read_sql_query(
            """SELECT account_id, symbol, qty, avg_cost_usd FROM agent_positions ORDER BY account_id, symbol""",
            conn,
        )
        if not pos.empty:
            pos["account_id"] = pos["account_id"].astype(str)
        curves = pd.read_sql_query(
            """SELECT account_id, datetime(ts) AS ts, equity_usd
               FROM agent_equity_snapshots
               WHERE datetime(ts) > datetime('now','-56 days')
               ORDER BY ts""",
            conn,
        )
        dec_map = _latest_decision_by_account(conn)
        for sid, ser in dec_map.items():
            foot = _format_decision_sources(conn, ser)
            if foot:
                source_hints[str(sid)] = foot
        today = _ny_today()
        trades_today = pd.read_sql_query(
            """SELECT account_id, COALESCE(action, side) AS action, symbol, ROUND(notional_usd, 2) AS usd,
                      COALESCE(NULLIF(TRIM(plain_reason), ''), reason) AS why_note,
                      COALESCE(risk_note, '') AS risk_note,
                      substr(ts, 12, 8) AS t_et
               FROM agent_trades
               WHERE substr(ts, 1, 10) = ?
               ORDER BY ts DESC""",
            conn,
            params=(today,),
        )
        if not trades_today.empty:
            trades_today["account_id"] = trades_today["account_id"].astype(str)
        tu = pd.read_sql_query(
            """SELECT datetime(created_at) AS t, agent_id, model, input_tokens,
                      output_tokens, success, reason_for_call
               FROM token_usage
               ORDER BY id DESC LIMIT 60""",
            conn,
        )
        token_totals = pd.read_sql_query(
            """SELECT agent_id, COUNT(*) AS n FROM token_usage
               WHERE substr(created_at, 1, 10) = ? GROUP BY agent_id""",
            conn,
            params=(today,),
        )
    finally:
        conn.close()

    syms = sorted({str(s).upper() for s in (pos["symbol"].tolist() if not pos.empty else [])})
    pxm: dict[str, float] = {}
    if syms:
        pxm = {q.symbol.upper(): float(q.px) for q in fetch_quotes(syms)}

    rows: list[dict] = []
    for aid in accounts["id"]:
        aid = str(aid)
        cash = float(accounts.loc[accounts["id"] == aid, "cash_usd"].iloc[0])
        mv = 0.0
        sub = pos[pos["account_id"] == aid] if not pos.empty else pd.DataFrame()
        desc_parts: list[str] = []
        for _, pr in sub.iterrows():
            sym = str(pr["symbol"]).upper()
            qty = float(pr["qty"])
            px = float(pxm.get(sym) or 0.0)
            v = qty * px
            mv += v
            if v >= 80:
                desc_parts.append(f"{sym}≈${v:,.0f}")
        equity = cash + mv
        rows.append(
            {
                "id": aid,
                "cash": cash,
                "positions_mv": round(mv, 2),
                "equity_live": round(equity, 2),
                "positions_short": "; ".join(desc_parts[:6]) or ("（空仓）" if cash > 4000 else ""),
            }
        )

    agg = pd.DataFrame(rows)
    view = accounts.merge(agg, on="id", how="left")

    tk_map: dict[str, int] = {}
    if not token_totals.empty:
        for _, rr in token_totals.iterrows():
            a = rr.get("agent_id")
            if pd.notna(a) and str(a).strip():
                tk_map[str(a)] = int(rr["n"])

    yaml_def = yaml_goal_defaults()
    st.caption(f"新开库默认投资目标见 `settings.yaml → agent_goal_defaults`：{dashboard_goal_line_zh(yaml_def)[:120]}…")

    bh_equity = (
        float(view.loc[view["id"] == "buyhold", "equity_live"].iloc[0])
        if "buyhold" in set(view["id"].astype(str))
        else None
    )

    st.subheader("核心三张卡片")
    conn_ro = get_conn(read_only=True)
    try:
        for aid in _CORE_ACCOUNTS:
            _render_one_card(
                conn=conn_ro,
                aid=aid,
                accounts=accounts,
                view=view,
                pos=pos,
                pxm=pxm,
                dec_map=dec_map,
                source_hints=source_hints,
                trades_today=trades_today,
                tk_map=tk_map,
                bh_equity=bh_equity,
            )

        extras = sorted(
            a for a in view["id"].astype(str).tolist() if a not in set(_CORE_ACCOUNTS)
        )
        if extras:
            with st.expander("高级：库里还有其它虚拟账户（当前 daemon 不会自动跑决策）", expanded=False):
                st.caption(
                    "第一版 orchestrator 只轮询 **`takeover-balanced`** 与 **`fresh-balanced`**。"
                    "若你仍看到 conservative / aggressive 等行，仅为历史 seed；需要可在后续版本重新加入循环。"
                )
                st.dataframe(
                    view[view["id"].isin(extras)][
                        ["id", "label", "mode", "style", "allow_trades", "cash", "positions_mv", "equity_live"]
                    ].rename(columns={"allow_trades": "可交易"}),
                    hide_index=True,
                    use_container_width=True,
                )

        # 名人 / 大佬参考：简述 + 跳转机会雷达页的完整图解
        st.subheader("名人持仓参考（占位）")
        st.markdown(
            "13F **季度滞后**、只适合学「结构上怎么分散」，不能当短线跟单信号。"
            "更完整的演示饼图与板块分配在 **「机会雷达」** 页底部；此处只保留一句话对照。"
        )
        bh_syms = pos[pos["account_id"] == "buyhold"]["symbol"].tolist() if not pos.empty else []
        if bh_syms:
            mega = {"MSFT", "GOOGL", "GOOG", "META", "NVDA", "AAPL", "AMZN", "AVGO"}
            hit = sum(1 for s in bh_syms if str(s).upper() in mega)
            if hit >= max(3, len(bh_syms) // 3):
                st.success(
                    "你的镜像仓位 **偏大盘科技权重**——可参考「大型科技 + 少许现金缓冲」这一类公开披露结构，"
                    "但要记得：你看到的是几个月前的模板，不等于他们今天还在加仓。"
                )
            else:
                st.info(
                    "你的镜像清单 **不单押 mega-cap**——可参考更分散的 long-only 风格模板，"
                    "重点学「单票上限、现金比例、行业分散」，不要逐票抄作业。"
                )
        else:
            st.caption("尚无 `buyhold` 持仓行；先同步 watchlist / 跑一遍建库与脉冲。")
    finally:
        conn_ro.close()

    if not curves.empty:
        only = [c for c in _CORE_ACCOUNTS if c in set(curves["account_id"].astype(str).unique())]
        subc = curves[curves["account_id"].isin(only)]
        if not subc.empty:
            with st.expander("权益快照曲线（仅核心三账户，脉冲后落库）", expanded=False):
                wide = subc.pivot_table(
                    index="ts", columns="account_id", values="equity_usd", aggfunc="last"
                )
                st.line_chart(wide.sort_index())

    if not tu.empty:
        with st.expander("最近 LLM token", expanded=False):
            st.dataframe(tu.head(40), hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("虚拟账户实验（独立 JSON 版）")
    a1, a2, a3 = st.columns(3)
    if a1.button("初始化镜像账户"):
        acc = init_mirror_account_from_real_positions()
        st.success(f"已初始化 {acc.name}")
    if a2.button("初始化 5k 账户"):
        acc = init_agent_5k_account()
        st.success(f"已初始化 {acc.name}")
    if a3.button("运行一次 agent 决策（两账户）"):
        r1 = run_agent_for_account("mirror_portfolio")
        r2 = run_agent_for_account("agent_5k")
        st.success(
            f"完成：mirror trades={len(r1.get('trades', []))}, "
            f"agent_5k trades={len(r2.get('trades', []))}"
        )

    for name in ("mirror_portfolio", "agent_5k"):
        acc = load_virtual_account(name)
        stats = compute_account_stats(acc)
        st.markdown(f"#### {name}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("总资产", f"${stats['total_value']:,.2f}")
        c2.metric("收益", f"${stats['pnl']:+,.2f}", delta=f"{stats['pnl_pct']:+.2f}%")
        c3.metric("回撤(粗)", f"{stats['max_drawdown_approx']:.2f}%")
        c4.metric("持仓/交易", f"{stats['positions']}/{stats['trades']}")
        if acc.positions:
            prow = []
            syms = sorted(acc.positions.keys())
            qmap = {q.symbol.upper(): q for q in fetch_quotes(syms)}
            for sym, p in acc.positions.items():
                px = float((qmap.get(sym).px if qmap.get(sym) else 0.0) or 0.0)
                shares = float(p.get("shares") or 0.0)
                mv = shares * px
                prow.append({"symbol": sym, "shares": shares, "avg_cost": p.get("avg_cost"), "mv_live": round(mv, 2)})
            st.dataframe(pd.DataFrame(prow).sort_values("mv_live", ascending=False), hide_index=True, use_container_width=True)
        if acc.trades:
            trows = [vars(t) for t in acc.trades[-10:]][::-1]
            st.dataframe(pd.DataFrame(trows), hide_index=True, use_container_width=True)
