from __future__ import annotations

import streamlit as st

from monitors.auto_watch_config import load_auto_watch_config, save_auto_watch_config
from monitors.market_radar import universe_stats


_SCOPE_LABELS = {
    "holdings": "我的持仓",
    "watchlist": "手动观察 / watchlist",
    "opportunity_pool": "选股池",
    "guru_pool": "大佬作业池",
    "revaluation_pool": "重估趋势池",
    "theme_pool": "主题池",
    "etf_pool": "ETF 推荐池",
}

_TYPE_LABELS = {
    "entry_range": "跌入推荐区间",
    "major_move": "重大异动",
    "theme_breakout": "主题同步启动",
    "holding_drawdown": "持仓回撤 / 持仓纪律",
    "major_news": "财报 / 重大新闻",
    "etf_market_drop": "ETF / 大盘急跌",
}


def render() -> None:
    st.subheader("🔔 自动提醒规则")
    st.caption(
        "Auto Watch Mode 默认帮你盯住持仓、观察池、选股池、大佬作业池和主题池。"
        "你加入池子后，系统自动生成默认提醒；手动提醒只是增强，不是必需。"
    )

    cfg = load_auto_watch_config()
    enabled = st.toggle("Auto Watch Mode", value=bool(cfg.get("enabled", True)))
    if enabled:
        st.success("自动提醒已开启：出现买入机会、重大异动、主题启动或持仓风险时会自动写入 alerts。")
    else:
        st.warning("自动提醒已关闭：系统不会自动写入新提醒，只保留手动提醒和已有数据。")

    st.markdown("#### 提醒范围")
    scopes = dict(cfg.get("scopes") or {})
    cols = st.columns(2)
    for i, (key, label) in enumerate(_SCOPE_LABELS.items()):
        with cols[i % 2]:
            scopes[key] = st.checkbox(label, value=bool(scopes.get(key, True)), key=f"scope_{key}")

    st.markdown("#### 提醒类型")
    types = dict(cfg.get("types") or {})
    cols = st.columns(2)
    for i, (key, label) in enumerate(_TYPE_LABELS.items()):
        with cols[i % 2]:
            types[key] = st.checkbox(label, value=bool(types.get(key, True)), key=f"type_{key}")

    st.markdown("#### 推送强度")
    strength_options = {
        "urgent_only": "只推 urgent",
        "urgent_major": "推 urgent + major",
        "all": "全部推送",
    }
    current = str(cfg.get("push_strength") or "urgent_major")
    strength_label = st.radio(
        "ntfy 推送范围",
        list(strength_options.values()),
        index=list(strength_options).index(current) if current in strength_options else 1,
        horizontal=True,
    )
    push_strength = next(k for k, v in strength_options.items() if v == strength_label)

    limits = dict(cfg.get("limits") or {})
    limits["max_auto_alerts_per_cycle"] = int(
        st.number_input("每轮最多自动提醒条数", value=int(limits.get("max_auto_alerts_per_cycle", 12)), min_value=1, max_value=50)
    )
    limits["max_theme_pushes_per_day"] = int(
        st.number_input("同一主题每天最多推送次数", value=int(limits.get("max_theme_pushes_per_day", 2)), min_value=1, max_value=10)
    )

    new_cfg = {
        "enabled": enabled,
        "scopes": scopes,
        "types": types,
        "push_strength": push_strength,
        "limits": limits,
    }
    if st.button("保存自动提醒设置", use_container_width=True):
        save_auto_watch_config(new_cfg)
        st.success("已保存。后台下一轮 market pulse 会使用这套规则。")

    st.divider()
    st.markdown("#### 当前自动监控范围")
    try:
        stats = universe_stats()
        st.metric("Radar Universe", stats.get("total", 0))
        st.json(stats.get("by_source", {}), expanded=False)
    except Exception as exc:
        st.warning(f"读取 radar universe 失败：{exc}")

    st.caption(
        "产品原则：AlphaWatch 不是让你给每只股票手动设提醒，而是你表达关心的主题/池子后，系统自动盯。"
    )
