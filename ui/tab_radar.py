from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from data_layer.smart_money import thirteenth_f_demo
from db.client import get_conn


def _parse_signals(blob: object) -> dict[str, Any]:
    if blob is None or (isinstance(blob, float) and pd.isna(blob)):
        return {}
    if isinstance(blob, dict):
        return blob
    s = str(blob).strip()
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _human_why(blurb: str | None, sig: dict[str, Any], score: float | int | None, bucket: str | None) -> str:
    parts: list[str] = []
    if blurb and str(blurb).strip():
        parts.append(str(blurb).strip())
    chg = sig.get("abs_daily_chg_pct")
    if isinstance(chg, (int, float)):
        parts.append(f"最近一次脉冲快照里日波幅约 ±{float(chg):.2f}% 量级（绝对值计入打分）")
    sg = sig.get("signal_peak_48h")
    if isinstance(sg, (int, float)) and float(sg) > 0:
        parts.append(f"过去约两天内相关 `market_signals` 最高分大约 {float(sg):.1f}")
    if score is not None:
        parts.append(f"合成关注分 {int(score)}（bucket: {bucket or '—'}）")
    return " ".join(parts).strip() or "规则池里的观测标的；分数由最近一次市场脉冲刷新。"


def _risk_sentence(chg: float | None, score_val: float | int | None) -> str:
    ch = float(chg or 0.0)
    sc = int(score_val or 0)
    tier = "波动可能很大，适合先观察再做功课" if ch >= 6 or sc >= 70 else "波动中等，别把观察名单当成喊单"
    return f"{tier}；单笔仓位宜小、设好最坏情况。"


def _load_agent_holdings_symbols(conn: object) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"fresh-balanced": set(), "takeover-balanced": set()}
    cur = conn.execute(
        """SELECT account_id, symbol, qty FROM agent_positions
           WHERE account_id IN ('fresh-balanced','takeover-balanced')"""
    )
    for r in cur.fetchall():
        aid = str(r["account_id"])
        try:
            q = float(r["qty"] or 0.0)
        except (TypeError, ValueError):
            q = 0.0
        if aid in out and q > 1e-9:
            out[aid].add(str(r["symbol"]).upper())
    return out


def _demo_reference_portfolio() -> dict[str, float]:
    """Static template — not any live filing."""
    return {
        "MSFT": 0.22,
        "GOOGL": 0.16,
        "NVDA": 0.14,
        "META": 0.10,
        "AVGO": 0.08,
        "ASML": 0.06,
        "CSCO": 0.06,
        "Cash / T-bill proxy": 0.18,
    }


_SECTOR_HINTS: dict[str, str] = {
    "MSFT": "Software / cloud",
    "GOOGL": "Advertising + cloud",
    "NVDA": "Semis / AI infra",
    "META": "Consumer internet platforms",
    "AVGO": "Semis infra",
    "ASML": "Semicap equipment",
    "CSCO": "Enterprise networking",
}


def _theme_of(row: pd.Series) -> str:
    txt = f"{row.get('blurb') or ''} {row.get('bucket') or ''}".lower()
    if any(k in txt for k in ("ai", "sem", "chip", "data center", "互联", "算力")):
        return "AI / 半导体链"
    if any(k in txt for k in ("power", "电力", "核", "smr", "能源")):
        return "电力 / 能源链"
    if any(k in txt for k in ("macro", "rates", "yield", "通胀")):
        return "宏观敏感链"
    return "其他主题"


def _tier_label(score: int, chg_abs: float) -> str:
    if score >= 70 or chg_abs >= 8:
        return "强势主线"
    if score >= 50 or chg_abs >= 4:
        return "二线主线"
    return "萌芽主线"


def _short_status(score: int, chg: float) -> str:
    if score >= 70 or chg >= 8:
        return "已启动"
    if score >= 45 and chg <= 3:
        return "待确认"
    if chg < -3:
        return "回撤中"
    return "观察"


def _next_step(score: int, chg: float, held_line: str) -> str:
    if held_line != "—":
        return "看减仓/加仓规则"
    if score >= 60 and chg <= 3:
        return "给 Fresh 复盘"
    if chg >= 8:
        return "别追，等回踩"
    return "继续盯"


def _latest_quote_change_map(conn: object, symbols: list[str]) -> dict[str, float]:
    syms = [s for s in symbols if s]
    if not syms:
        return {}
    ph = ",".join(["?"] * len(syms))
    rows = conn.execute(
        f"""
        SELECT mq.symbol, mq.chg_pct
        FROM market_quotes mq
        INNER JOIN (
          SELECT symbol, MAX(retrieved_at) AS mx
          FROM market_quotes
          WHERE symbol IN ({ph})
          GROUP BY symbol
        ) t ON mq.symbol = t.symbol AND mq.retrieved_at = t.mx
        """,
        tuple(syms),
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        try:
            out[str(r["symbol"]).upper()] = float(r["chg_pct"] or 0.0)
        except (TypeError, ValueError):
            out[str(r["symbol"]).upper()] = 0.0
    return out


def render() -> None:
    st.subheader("风口雷达")
    st.info(
        "这里是 **风口分级 + 补涨线索**，不是喊单。"
        "你看到的是规则脉冲后的观测结论：先看主线，再看同链条里还没动的候选。"
    )

    conn = get_conn(read_only=True)
    try:
        df = pd.read_sql_query(
            """SELECT ticker, score, bucket, blurb, signals_json, updated_at
               FROM opportunity_candidates ORDER BY score DESC""",
            conn,
        )
        recent_bundle = pd.read_sql_query(
            """SELECT datetime(created_at) AS t, COALESCE(symbol,'') AS symbol, signal_type, score
               FROM market_signals
               WHERE signal_type IN ('signal_bundle','earnings_beat','company_headline','price_breakout')
               ORDER BY id DESC LIMIT 80""",
            conn,
        )
        held = _load_agent_holdings_symbols(conn)
        takeover_syms = {
            str(r["symbol"]).upper()
            for r in conn.execute(
                "SELECT symbol FROM agent_positions WHERE account_id = 'takeover-balanced'"
            ).fetchall()
        }
        qmap = _latest_quote_change_map(conn, [str(x).upper() for x in df["ticker"].tolist()] if not df.empty else [])
    finally:
        conn.close()

    if df.empty:
        st.warning("暂无数据；跑一次「推送中心 → 市场脉冲」或依赖 `opportunity_seed`。")
        display = pd.DataFrame()
    else:
        meta_rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            sig = _parse_signals(row.get("signals_json"))
            sym = str(row["ticker"]).upper()
            chg = qmap.get(sym)
            if chg is None:
                raw = sig.get("abs_daily_chg_pct")
                chg = float(raw) if isinstance(raw, (int, float)) else 0.0
            chgf = float(chg)
            fresh_on = "Fresh $5k 已持有" if sym in held["fresh-balanced"] else "—"
            take_on = "Takeover 已持有" if sym in held["takeover-balanced"] else "—"
            agent_bits = []
            if fresh_on != "—":
                agent_bits.append(fresh_on)
            if take_on != "—":
                agent_bits.append(take_on)
            overlap = ""
            if sym in takeover_syms:
                overlap = "这只在你的 Takeover 镜像里；雷达更多是提醒 agent 是否该重新看风险/新闻。"

            score_i = int(row["score"] or 0)
            theme = _theme_of(row)
            held_line = " / ".join(agent_bits) if agent_bits else "—"
            meta_rows.append(
                {
                    "ticker": sym,
                    "score": score_i,
                    "today_chg_pct": round(chgf, 2),
                    "theme": theme,
                    "tier": _tier_label(score_i, abs(chgf)),
                    "bucket": row.get("bucket"),
                    "status": _short_status(score_i, chgf),
                    "agent": held_line,
                    "next_step": _next_step(score_i, chgf, held_line),
                    "为什么会在雷达里": _human_why(
                        str(row.get("blurb") or "") if row.get("blurb") is not None else None,
                        sig,
                        row.get("score"),
                        str(row.get("bucket") or "") if row.get("bucket") is not None else None,
                    ),
                    "和新闻/行业怎么连": overlap
                    or "与 `market_signals`、关注池主题、当日波动共振；详见 blurb / 上游规则。",
                    "风险一句话": _risk_sentence(chgf, row.get("score")),
                    "上次更新": row.get("updated_at"),
                }
            )
        display = pd.DataFrame(meta_rows)

    st.markdown("##### 主线分级")
    if not display.empty:
        for tier in ("强势主线", "二线主线", "萌芽主线"):
            sub = display[display["tier"] == tier].copy()
            if sub.empty:
                continue
            st.markdown(f"**{tier}**")
            agg = (
                sub.groupby("theme", as_index=False)
                .agg(avg_score=("score", "mean"), max_chg=("today_chg_pct", "max"), members=("ticker", lambda x: ", ".join(sorted(set(x)))))
                .sort_values(["avg_score", "max_chg"], ascending=False)
            )
            agg["avg_score"] = agg["avg_score"].round(1)
            st.dataframe(agg.rename(columns={"theme": "主题", "avg_score": "平均分", "max_chg": "当日最大涨跌%", "members": "代表股"}), hide_index=True, use_container_width=True)

        st.markdown("##### 早期补涨候选")
        cands: list[dict[str, Any]] = []
        for theme, grp in display.groupby("theme"):
            g2 = grp.sort_values("today_chg_pct", ascending=False)
            if g2.empty:
                continue
            leader = g2.iloc[0]
            if float(leader["today_chg_pct"]) < 8.0:
                continue
            for _, r in g2.iloc[1:].iterrows():
                if float(r["today_chg_pct"]) <= 3.0:
                    cands.append(
                        {
                            "theme": theme,
                            "leader": f"{leader['ticker']} {float(leader['today_chg_pct']):+.2f}%",
                            "candidate": str(r["ticker"]),
                            "candidate_chg": float(r["today_chg_pct"]),
                            "logic": f"同主题里龙头已明显启动，{r['ticker']} 仍相对滞后。",
                            "risk": "补涨不一定发生，注意追高与回撤。",
                        }
                    )
        if cands:
            st.dataframe(pd.DataFrame(cands).rename(columns={"theme": "主题", "leader": "龙头", "candidate": "补涨候选", "candidate_chg": "当日涨跌%", "logic": "逻辑", "risk": "风险"}), hide_index=True, use_container_width=True)
        else:
            st.caption("暂未发现“龙头明显启动 + 同主题滞后”的清晰补涨组合。")

        st.markdown("##### 风口日历（近 7 天）")
        if not recent_bundle.empty:
            cal = recent_bundle.copy()
            cal["date"] = cal["t"].astype(str).str.slice(0, 10)
            cal = cal.rename(columns={"symbol": "ticker", "signal_type": "event", "score": "strength"})
            st.dataframe(cal[["date", "ticker", "event", "strength"]].head(30), hide_index=True, use_container_width=True)
    else:
        st.caption("暂无风口分级数据。")

    st.markdown("##### 候选池")
    if not display.empty:
        compact = display[
            ["ticker", "theme", "tier", "status", "today_chg_pct", "agent", "next_step"]
        ].rename(
            columns={
                "ticker": "代码",
                "theme": "主题",
                "tier": "级别",
                "status": "状态",
                "today_chg_pct": "日涨跌%",
                "agent": "账户",
                "next_step": "下一步",
            }
        )
        st.dataframe(compact, hide_index=True, use_container_width=True, height=min(420, 72 + 35 * len(compact)))

        with st.expander("候选详情", expanded=False):
            detail = display[
                ["ticker", "为什么会在雷达里", "和新闻/行业怎么连", "风险一句话", "上次更新"]
            ].rename(
                columns={
                    "ticker": "代码",
                    "为什么会在雷达里": "触发原因",
                    "和新闻/行业怎么连": "关联",
                    "风险一句话": "风险",
                    "上次更新": "更新",
                }
            )
            st.dataframe(detail, hide_index=True, use_container_width=True)
    st.caption(
        "想确认 agent 是否真的下手：可在终端查 `agent_decisions` / `agent_trades`，"
        "或看「虚拟实验」里对应卡片。"
    )

    st.divider()
    st.markdown("##### 名人 / 大佬持仓参考（演示，不可当实时信号）")
    st.warning(
        "SEC **13F 至少滞后约一个季度**。下面饼图是 **虚构的分散科技龙头模板**，"
        "用来学「结构上如何配大盘科技 + 现金缓冲」——不是「某人刚刚买了所以我也买」。"
    )

    ref = _demo_reference_portfolio()
    demo_df = pd.DataFrame({"ticker": list(ref.keys()), "weight_pct": [v * 100 for v in ref.values()]})

    mega = {"MSFT", "GOOGL", "GOOG", "META", "NVDA", "AAPL", "AMZN", "AVGO"}
    if takeover_syms:
        hit = sum(1 for s in takeover_syms if s in mega)
        if hit >= max(2, len(takeover_syms) // 3):
            st.success(
                "你的 **Takeover 镜像**里有多只 mega-cap 科技龙头——风格接近「大盘科技成长」公开模板；"
                "可重点比较 **单票集中度**、**现金比例**，而不是跟买具体代码。"
            )
        else:
            st.info(
                "你的镜像组合 **不集中押 mega-cap**——可参考更平衡的长线多头模板，学习 **行业上限** 与 **下行缓冲**，"
                "别用 13F 当短线 trigger。"
            )
    else:
        st.caption("还没有 Takeover 镜像持仓；先同步清单并建库。")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**演示 Top weights**")
        st.dataframe(demo_df, hide_index=True, use_container_width=True)
    with col_b:
        st.markdown("**演示板块（粗分）**")
        sec_rows = [
            {"sector": _SECTOR_HINTS.get(t, "Other / cash"), "weight_pct": w * 100}
            for t, w in ref.items()
        ]
        sec_df = pd.DataFrame(sec_rows).groupby("sector", as_index=False)["weight_pct"].sum()
        st.dataframe(sec_df.sort_values("weight_pct", ascending=False), hide_index=True, use_container_width=True)

    try:
        import plotly.express as px

        fig = px.pie(demo_df, names="ticker", values="weight_pct", title="Demo allocator (not live 13F)")
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.caption("未安装 plotly 时跳过饼图；`pip install plotly` 后刷新即可。")

    st.markdown(
        "**可以学什么**：如何用宽基龙头 + 现金控制回撤；如何把单票限额写进纪律。  \n"
        "**不要照抄什么**：披露时间、持仓细节、杠杆、费率与你完全不同；它不是交易信号。"
    )

    with st.expander("开发者：占位 JSON（thirteenth_f_demo）", expanded=False):
        st.json([vars(x) for x in thirteenth_f_demo()])
