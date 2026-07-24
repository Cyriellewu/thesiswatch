"""本地自绘 K 线图(Plotly)——不依赖任何外部嵌入,保证能显示。

一张图三层:
- 价格:K 线 + MA50 + MA200
- MACD:柱状(动能)+ MACD 线 + 信号线
- RSI:14 日,带 70/30 参考线

数据来自 signals.indicators.load_daily_bars(yfinance,已缓存)。离线时返回 None。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from signals.indicators import load_daily_bars, macd_hist, rsi


@st.cache_data(ttl=300, show_spinner=False)
def _bars(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    h = load_daily_bars(symbol, period=period)
    if h is None or getattr(h, "empty", True) or "Close" not in getattr(h, "columns", []):
        return None
    # 只保留能序列化的 OHLCV,方便 st.cache_data
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in h.columns]
    out = h[cols].copy()
    out.index = pd.to_datetime(out.index)
    return out


def build_figure(symbol: str, months: int = 6) -> go.Figure | None:
    h = _bars(symbol, period="1y")
    if h is None or len(h) < 30:
        return None

    close = h["Close"].astype(float)
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    macd_line, sig, hist = macd_hist(close)
    r = rsi(close, 14)

    # 展示窗口:最近 N 个月(均线用全量算好再切,保证准确)
    show = max(40, months * 21)
    h = h.tail(show)
    idx = h.index
    close_s = close.tail(show)
    ma50_s = ma50.tail(show)
    ma200_s = ma200.tail(show)
    macd_s = macd_line.tail(show)
    sig_s = sig.tail(show)
    hist_s = hist.tail(show)
    rsi_s = r.tail(show)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=(f"{symbol} 日线 · MA50/MA200", "MACD 动能", "RSI(14)"),
    )

    fig.add_trace(
        go.Candlestick(
            x=idx, open=h["Open"], high=h["High"], low=h["Low"], close=h["Close"],
            name="K线", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            showlegend=False,
        ), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=ma50_s, name="MA50", line=dict(color="#f5c518", width=1.3)), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=ma200_s, name="MA200", line=dict(color="#42a5f5", width=1.3)), row=1, col=1)

    hist_colors = ["#26a69a" if v >= 0 else "#ef5350" for v in hist_s.fillna(0)]
    fig.add_trace(go.Bar(x=idx, y=hist_s, name="MACD柱", marker_color=hist_colors, showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=idx, y=macd_s, name="MACD", line=dict(color="#f5c518", width=1)), row=2, col=1)
    fig.add_trace(go.Scatter(x=idx, y=sig_s, name="Signal", line=dict(color="#42a5f5", width=1)), row=2, col=1)

    fig.add_trace(go.Scatter(x=idx, y=rsi_s, name="RSI", line=dict(color="#ab47bc", width=1.2), showlegend=False), row=3, col=1)
    fig.add_hline(y=70, line=dict(color="#ef5350", width=1, dash="dash"), row=3, col=1)
    fig.add_hline(y=30, line=dict(color="#26a69a", width=1, dash="dash"), row=3, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=620,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    fig.update_yaxes(range=[0, 100], row=3, col=1)
    return fig


def render(symbol: str) -> bool:
    """画出 symbol 的本地大图;成功返回 True,无数据返回 False。"""
    fig = build_figure(symbol)
    if fig is None:
        return False
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    return True


def donut(items: list[tuple[str, float]], title: str = "", highlight: set[str] | None = None,
          height: int = 340) -> go.Figure:
    """环状持仓图。items=[(标签, 权重%)];highlight 中的标签会被拉出并加 ⭐。"""
    highlight = {h.upper() for h in (highlight or set())}
    labels = [x[0] for x in items]
    values = [x[1] for x in items]
    pulls = [0.08 if lab.upper() in highlight else 0.0 for lab in labels]
    text = [f"⭐{lab}" if lab.upper() in highlight else lab for lab in labels]

    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.55, pull=pulls, text=text,
        textinfo="text+percent", textposition="outside",
        hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
        sort=True, direction="clockwise",
    ))
    fig.update_layout(
        template="plotly_dark", height=height,
        margin=dict(l=10, r=10, t=40 if title else 10, b=10),
        title=dict(text=title, x=0.5, font=dict(size=14)),
        showlegend=False,
    )
    return fig


def factor_bars(factors: list[dict], title: str = "", height: int = 260) -> go.Figure:
    """Horizontal attribution bars for a conviction score: green = +points,
    red = -points. `factors` = [{"label","detail","delta"}] (from conviction)."""
    factors = sorted(factors or [], key=lambda f: f.get("delta", 0))
    labels = [f.get("label", "") for f in factors]
    deltas = [float(f.get("delta", 0)) for f in factors]
    details = [f.get("detail", "") for f in factors]
    colors = ["#26a69a" if d >= 0 else "#ef5350" for d in deltas]
    fig = go.Figure(go.Bar(
        x=deltas, y=labels, orientation="h", marker_color=colors,
        text=[f"{d:+.0f}" for d in deltas], textposition="outside",
        customdata=details,
        hovertemplate="%{y}: %{x:+.1f} 分<br>%{customdata}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark", height=height,
        margin=dict(l=10, r=10, t=40 if title else 10, b=10),
        title=dict(text=title, x=0.5, font=dict(size=14)),
        xaxis=dict(title="对信念分的贡献", zeroline=True, zerolinecolor="#888"),
        showlegend=False, bargap=0.35,
    )
    return fig
