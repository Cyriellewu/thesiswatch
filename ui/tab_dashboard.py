"""📈 股票墙（Visual Dashboard）

- 3 列网格：每只持仓一张免费 TradingView 迷你图（价格 + 涨跌 + 迷你走势）
- 点「🔍 放大」→ 完整 Advanced Chart（K 线 + MACD + RSI）+ 新手买卖表
- 简易指标表：价 vs MA50/MA200、RSI、MACD 动能、52 周位置（大白话解释）
- 价格提醒：设「突破上方 / 跌破下方」→ 命中走现有 ntfy 推送
- 三位 AI 顾问（稳健 / 均衡 / 激进）对持仓点评（规则化，$0；可选 LLM 增强）
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from data_layer.market_data import fetch_quotes
from data_layer.universe import load_watchlist_tickers
from tasks import holding_commentary as hc
from tasks import price_alarms as pa

# 除持仓外，允许对这些「关注名」也设提醒 / 放大
_WATCH_EXTRA = ["SMH", "SOXX", "TSM", "CRWV", "BE", "SMR", "AMZN", "AAPL", "MU", "AMD", "VOO", "SPAXX"]

_MINI_TPL = """
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js" async>
  {
  "symbol": "__SYMBOL__",
  "width": "100%",
  "height": 180,
  "locale": "en",
  "dateRange": "3M",
  "colorTheme": "dark",
  "isTransparent": false,
  "autosize": false,
  "largeChartUrl": ""
  }
  </script>
</div>
"""

_ADV_TPL = """
<div class="tradingview-widget-container" style="height:560px;width:100%">
  <div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
  {
  "symbol": "__SYMBOL__",
  "interval": "D",
  "timezone": "Asia/Shanghai",
  "theme": "dark",
  "style": "1",
  "locale": "en",
  "hide_side_toolbar": false,
  "allow_symbol_change": false,
  "studies": ["STD;MACD", "STD;RSI"],
  "support_host": "https://www.tradingview.com",
  "width": "100%",
  "height": 560
  }
  </script>
</div>
"""

_TA_TPL = """
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js" async>
  {
  "interval": "1D",
  "width": "100%",
  "isTransparent": false,
  "height": 400,
  "symbol": "__SYMBOL__",
  "showIntervalTabs": true,
  "displayMode": "single",
  "locale": "en",
  "colorTheme": "dark"
  }
  </script>
</div>
"""


def _mini(symbol: str) -> None:
    components.html(_MINI_TPL.replace("__SYMBOL__", symbol), height=200)


def _advanced(symbol: str) -> None:
    components.html(_ADV_TPL.replace("__SYMBOL__", symbol), height=580)


def _ta_gauge(symbol: str) -> None:
    components.html(_TA_TPL.replace("__SYMBOL__", symbol), height=420)


@st.cache_data(ttl=300, show_spinner=False)
def _snapshots(symbols: tuple[str, ...]) -> list[dict]:
    out = []
    for s in symbols:
        snap = hc.symbol_indicator_snapshot(s)
        if snap:
            out.append(snap)
    return out


def _fmt(v, suffix: str = "", digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _indicator_table(snaps: list[dict]) -> pd.DataFrame:
    rows = []
    for s in snaps:
        px = s.get("px")
        ma50 = s.get("ma50")
        ma200 = s.get("ma200")
        vs50 = "↑ 上方" if (px and ma50 and px >= ma50) else ("↓ 下方" if (px and ma50) else "—")
        vs200 = "↑ 上方" if (px and ma200 and px >= ma200) else ("↓ 下方" if (px and ma200) else "—")
        rsi_v = s.get("rsi")
        rsi_note = ""
        if rsi_v is not None:
            rsi_note = " 超买" if rsi_v >= 70 else (" 超卖" if rsi_v <= 30 else "")
        mh = s.get("macd_hist")
        mh_prev = s.get("macd_hist_prev")
        if mh is None or mh_prev is None:
            macd_s = "—"
        elif mh >= 0 and mh >= mh_prev:
            macd_s = "🟢 向上"
        elif mh < 0 and mh <= mh_prev:
            macd_s = "🔴 向下"
        else:
            macd_s = "🟡 转折"
        rows.append(
            {
                "股票": s["symbol"],
                "现价": _fmt(px),
                "今日": _fmt(s.get("chg_pct"), "%"),
                "vs MA50": vs50,
                "vs MA200": vs200,
                "RSI": (f"{rsi_v:.0f}{rsi_note}" if rsi_v is not None else "—"),
                "MACD动能": macd_s,
                "52周位置": _fmt(s.get("pos_52"), "%", 0),
            }
        )
    return pd.DataFrame(rows)


def _positions_qty() -> dict[str, float]:
    try:
        from data_layer.portfolio_analytics import load_positions  # noqa: PLC0415

        root = Path(__file__).resolve().parents[1]
        pos = load_positions(root / "config" / "watchlist.yaml")
        return {p.ticker.upper(): float(p.qty) for p in pos}
    except Exception:
        return {}


def render() -> None:
    st.subheader("📈 股票墙 · 一眼看盘")
    st.caption(
        "免费 TradingView 图表 + 简易指标 + 价格提醒 + 三位 AI 顾问点评。"
        "图只做展示与研究，不代下单。"
    )

    holdings = load_watchlist_tickers()
    if not holdings:
        st.info("`config/watchlist.yaml` 里还没有持仓。")
        return

    qty = _positions_qty()

    if "dash_zoom" not in st.session_state:
        st.session_state["dash_zoom"] = holdings[0]

    # ---------- 网格：每行 3 只 ----------
    st.markdown("#### 我的持仓（点「🔍 放大」看大图）")
    per_row = 3
    for i in range(0, len(holdings), per_row):
        cols = st.columns(per_row)
        for col, sym in zip(cols, holdings[i : i + per_row]):
            with col:
                q = qty.get(sym.upper())
                label = f"**{sym}**" + (f" · {q:g} 股" if q else "")
                st.markdown(label)
                _mini(sym)
                if st.button(f"🔍 放大 {sym}", key=f"zoom_{sym}", use_container_width=True):
                    st.session_state["dash_zoom"] = sym

    st.divider()

    # ---------- 放大区 ----------
    zoom = st.session_state.get("dash_zoom") or holdings[0]
    picker_opts = holdings + [s for s in _WATCH_EXTRA if s not in holdings]
    zoom = st.selectbox(
        "放大查看",
        picker_opts,
        index=picker_opts.index(zoom) if zoom in picker_opts else 0,
    )
    st.session_state["dash_zoom"] = zoom

    big, gauge = st.columns([3, 1])
    with big:
        st.markdown(f"##### {zoom} · 日线（含 MACD + RSI）")
        _advanced(zoom)
    with gauge:
        st.markdown("##### 新手买卖表")
        st.caption("综合多项指标给的强弱信号，仅供参考。")
        _ta_gauge(zoom)

    # ---------- 简易指标表 ----------
    st.divider()
    st.markdown("#### 简易指标表")
    with st.expander("怎么看这些指标？（点开，大白话版）", expanded=False):
        st.markdown(
            "- **MA50 / MA200**：50 天 / 200 天平均价。价格在两条线**上方**=趋势健康；"
            "跌到 **MA200 下方**是要警惕的信号。\n"
            "- **RSI**：0–100 的一个数。>70=涨太快可能要歇，<30=跌太多可能超卖。\n"
            "- **MACD 动能**：🟢向上=动能转强，🔴向下=动能转弱，🟡转折=方向不明。\n"
            "- **52 周位置**：现价在过去一年高低区间里的位置。越接近 100%=越贵，越接近 0%=越便宜。"
        )
    if st.button("📊 加载 / 刷新指标（联网抓一年日线，约几秒）"):
        st.session_state["dash_load_ind"] = True
    if st.session_state.get("dash_load_ind"):
        with st.spinner("抓取行情并计算指标…"):
            snaps = _snapshots(tuple(holdings))
        if snaps:
            st.dataframe(_indicator_table(snaps), use_container_width=True, hide_index=True)
        else:
            st.info("暂时取不到行情（可能离线或被限流）。稍后再试。")

    # ---------- 价格提醒 ----------
    st.divider()
    st.markdown("#### 🔔 价格提醒")
    st.caption("命中后通过你已配置的 ntfy 推到手机；触发一次自动关闭，可再开启。")
    with st.form("add_alarm", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 2])
        with c1:
            a_sym = st.selectbox("股票", picker_opts, key="alarm_sym")
        with c2:
            a_dir = st.radio("方向", ["突破上方", "跌破下方"], horizontal=False, key="alarm_dir")
        with c3:
            a_px = st.number_input("目标价 $", min_value=0.0, step=1.0, format="%.2f", key="alarm_px")
        with c4:
            a_note = st.text_input("备注（可选）", key="alarm_note")
        submitted = st.form_submit_button("➕ 添加提醒", use_container_width=True)
        if submitted:
            if a_px and a_px > 0:
                direction = "above" if a_dir == "突破上方" else "below"
                try:
                    pa.add_alarm(a_sym, direction, float(a_px), a_note)
                    st.success(f"已添加：{a_sym} {a_dir} ${a_px:,.2f}")
                except Exception as exc:
                    st.error(f"添加失败：{exc}")
            else:
                st.warning("请填写大于 0 的目标价。")

    alarms = pa.list_alarms(active_only=False)
    active = [a for a in alarms if a.get("active")]
    done = [a for a in alarms if not a.get("active")]

    if st.button("🔎 立即检查一次（命中即推送）"):
        with st.spinner("对比现价…"):
            hit = pa.check_alarms(send=True)
        if hit:
            st.success("触发并已推送：" + "，".join(f"{h['ticker']}→${h['last_price']:,.2f}" for h in hit))
        else:
            st.info("暂无触发。")

    if active:
        st.markdown("**生效中：**")
        for a in active:
            arrow = "≥" if a["direction"] == "above" else "≤"
            cc = st.columns([5, 1])
            with cc[0]:
                note = f" · {a['note']}" if str(a.get("note") or "").strip() else ""
                st.write(f"🔔 **{a['ticker']}** {arrow} ${float(a['target_price']):,.2f}{note}")
            with cc[1]:
                if st.button("删除", key=f"del_{a['id']}"):
                    pa.delete_alarm(int(a["id"]))
                    st.rerun()
    else:
        st.caption("暂无生效中的提醒。")

    if done:
        with st.expander(f"已触发 / 已关闭（{len(done)}）", expanded=False):
            for a in done:
                arrow = "≥" if a["direction"] == "above" else "≤"
                last = f"（触发价 ${float(a['last_price']):,.2f}）" if a.get("last_price") else ""
                cc = st.columns([4, 1, 1])
                with cc[0]:
                    st.write(f"✅ {a['ticker']} {arrow} ${float(a['target_price']):,.2f} {last}")
                with cc[1]:
                    if st.button("重开", key=f"re_{a['id']}"):
                        pa.set_active(int(a["id"]), True)
                        st.rerun()
                with cc[2]:
                    if st.button("删除", key=f"delx_{a['id']}"):
                        pa.delete_alarm(int(a["id"]))
                        st.rerun()

    # ---------- 三位 AI 顾问 ----------
    st.divider()
    st.markdown("#### 🤖 三位 AI 顾问怎么看")
    st.caption("同一份持仓，稳健 / 均衡 / 激进三种风格各给结论。默认规则化、$0、可离线。")
    use_llm = st.checkbox("用 AI 润色成更自然的中文（需已配置 LLM key）", value=False)
    if st.button("让顾问点评我的持仓"):
        with st.spinner("计算指标并生成点评…"):
            commentary = hc.generate_commentary(holdings)
            if use_llm:
                commentary = hc.llm_enhance(commentary)
        st.session_state["dash_commentary"] = commentary

    commentary = st.session_state.get("dash_commentary")
    if commentary:
        tabs = st.tabs(["🛡️ 稳健派", "⚖️ 均衡派", "🚀 激进派", "📄 指标事实"])
        personas = commentary.get("personas", {})
        with tabs[0]:
            st.markdown(personas.get("conservative", "—"))
        with tabs[1]:
            st.markdown(personas.get("balanced", "—"))
        with tabs[2]:
            st.markdown(personas.get("aggressive", "—"))
        with tabs[3]:
            for line in commentary.get("facts", []):
                st.write("• " + line)
        if commentary.get("llm_text"):
            st.markdown("---")
            st.markdown("**AI 润色版：**")
            st.markdown(commentary["llm_text"])
        st.caption(f"来源：{commentary.get('source')}｜规则化点评不构成投资建议。")
