from __future__ import annotations

import json

import streamlit as st

from db.client import get_conn
from monitors.market_radar import radar_debug, scan_market_radar, universe_stats
from monitors.quote_format import format_radar_price_line
from monitors.watchlist_sentinels import add_sentinel, is_sentinel_starred


def render_major_moves(*, limit: int = 6, compact: bool = False) -> None:
    try:
        moves = scan_market_radar(max_moves=limit)
    except Exception as exc:
        st.warning(f"重大异动雷达暂不可用：{exc}")
        return

    st.markdown("### 🔥 今日重大异动")
    if not moves:
        st.caption("当前 radar universe 里没有触发 major/urgent 的异动。")
        return

    show = [m for m in moves if m.severity in {"urgent", "major"}] or moves[:limit]
    for m in show[:limit]:
        p_lbl, a_lbl, _line = format_radar_price_line(
            symbol=m.symbol,
            price=m.price,
            day_change_pct=m.day_change_pct,
            day_change_abs=m.day_change_abs_usd,
        )
        with st.container(border=True):
            a, b, c = st.columns([1.1, 1.4, 3])
            a.metric(m.symbol, p_lbl, f"{m.day_change_pct:+.2f}%")
            b.write(f"**{m.severity}** · {a_lbl}")
            b.caption(" / ".join(m.source_tags))
            if getattr(m, "is_theme_signal", False):
                b.caption("theme signal · 不直接买")
            c.write(m.possible_reason)
            c.caption(f"{m.bucket} · {' / '.join(m.themes[:3])}")
            if getattr(m, "discovered_by_theme_expansion", False):
                c.caption(f"发现来源：由 {getattr(m, 'expansion_theme', '主题')} 主题扩展发现")
            else:
                c.caption("发现来源：静态 radar universe / watchlist / theme pool")
            if not compact:
                c.info(f"动作：{m.action}")
                btn_col, news_col = c.columns([1, 1])
                conn = get_conn(read_only=False)
                try:
                    starred = is_sentinel_starred(conn, m.symbol)
                finally:
                    conn.close()
                if starred:
                    btn_col.button("✅ 已关注", key=f"major_move_starred_{m.symbol}", disabled=True)
                elif btn_col.button("⭐ 加入关注（自动盯回调）", key=f"major_move_star_{m.symbol}"):
                    conn = get_conn(read_only=False)
                    try:
                        add_sentinel(conn, m.symbol)
                    finally:
                        conn.close()
                    st.success(f"✅ 已关注 {m.symbol}，系统会自动盯合理价位和大跌回调。")
                    st.rerun()
                if m.news_evidence and news_col.button("📰 看完整新闻", key=f"major_move_news_{m.symbol}"):
                    st.session_state[f"major_move_show_news_{m.symbol}"] = True
                with st.expander("完整决策卡", expanded=False):
                    st.write(f"**今日异动**：{_line}")
                    st.write(f"**来源标签**：{' / '.join(m.source_tags)}")
                    st.write(f"**主题**：{' / '.join(m.themes[:5]) or m.bucket}")
                    st.write(f"**建议**：{m.action}")
                    if getattr(m, "is_theme_signal", False):
                        st.warning("这是 theme signal，不是直接买入候选。重点看同主题可交易替代标的。")
                    if getattr(m, "alternatives", None):
                        st.caption("替代关注：" + " / ".join(m.alternatives[:6]))
                if m.news_evidence:
                    expanded = bool(st.session_state.get(f"major_move_show_news_{m.symbol}", False))
                    with st.expander("新闻证据", expanded=expanded):
                        for e in m.news_evidence[:3]:
                            text = e.get("one_line_zh") or e.get("title") or ""
                            url = e.get("url") or ""
                            src = e.get("source") or ""
                            if url:
                                st.write(f"- [{text}]({url}) · {src}")
                            else:
                                st.write(f"- {text} · {src}")


def render_alert_debug() -> None:
    st.markdown("### Alert Debug")
    try:
        stats = universe_stats()
        st.write(f"radar_universe 总数：**{stats.get('total', 0)}**")
        st.json(stats.get("by_source", {}), expanded=False)
    except Exception as exc:
        st.warning(f"Universe 统计失败：{exc}")
    sym = st.text_input("输入 ticker / 公司名 / alias 查雷达链路", value="SNDK").strip()
    if not sym:
        return
    if st.button("检查雷达链路", key=f"radar_debug_{sym.upper()}"):
        try:
            data = radar_debug(sym)
            st.code(json.dumps(data, ensure_ascii=False, indent=2), language="json")
        except Exception as exc:
            st.error(f"Debug 失败：{exc}")
