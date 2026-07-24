"""持仓「简易指标」快照 + 三位 AI 顾问（稳健 / 均衡 / 激进）规则化点评。

设计目标：
- 默认**纯规则**，无需任何 API key、$0 成本、可离线（有行情时更准）。
- 指标只保留初学者友好的四个：MA50 / MA200（趋势）、RSI（超买超卖）、
  MACD 动能、52 周位置。
- 可选：若配置了 LLM key，可用 `llm_enhance` 生成更自然的中文点评。
"""

from __future__ import annotations

from typing import Any

from signals.indicators import load_daily_bars, macd_hist, rsi

# 角色分类（与 ui/tab_holdings 的口径保持一致，避免跨层 import UI）
_CORE_ETF = {"VOO", "VTI", "SPY", "QQQ", "QQQM", "SMH", "SOXX"}
_MEGA_GROWTH = {"MSFT", "GOOGL", "GOOG", "META", "NVDA", "AVGO", "AAPL", "AMZN", "TSM", "PANW", "MU", "AMD"}
_SATELLITE = {"IREN", "TSLA", "CRWV", "BE", "SMR", "OKLO", "COIN", "MSTR"}


def _role_of(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    if s in _CORE_ETF:
        return "core"
    if s in _MEGA_GROWTH:
        return "mega"
    if s in _SATELLITE:
        return "satellite"
    return "other"


def _finite(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 过滤 NaN


def symbol_indicator_snapshot(symbol: str, bars: Any | None = None) -> dict[str, Any]:
    """返回单只股票的简易指标快照；无行情时返回 {}。"""

    sym = str(symbol or "").upper().strip()
    h = bars if bars is not None else load_daily_bars(sym, period="1y")
    if h is None or getattr(h, "empty", True) or "Close" not in getattr(h, "columns", []):
        return {}

    close = h["Close"].astype(float)
    if len(close) < 2:
        return {}
    px = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    chg_pct = (px / prev - 1.0) * 100.0 if prev else 0.0

    ma50 = _finite(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    ma200 = _finite(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    r = rsi(close, 14)
    rsi_v = _finite(r.iloc[-1]) if len(r) else None

    _, _, mh = macd_hist(close)
    mh_last = _finite(mh.iloc[-1]) if len(mh) else None
    mh_prev = _finite(mh.iloc[-2]) if len(mh) > 1 else mh_last

    win = close.tail(252)
    hi_52 = float(win.max())
    lo_52 = float(win.min())
    pos_52 = (px - lo_52) / (hi_52 - lo_52) * 100.0 if hi_52 > lo_52 else 50.0

    return {
        "symbol": sym,
        "role": _role_of(sym),
        "px": px,
        "chg_pct": chg_pct,
        "ma50": ma50,
        "ma200": ma200,
        "rsi": rsi_v,
        "macd_hist": mh_last,
        "macd_hist_prev": mh_prev,
        "high_52": hi_52,
        "low_52": lo_52,
        "pos_52": pos_52,
    }


def _tags(snap: dict[str, Any]) -> dict[str, bool]:
    px = snap.get("px")
    ma50 = snap.get("ma50")
    ma200 = snap.get("ma200")
    rsi_v = snap.get("rsi")
    mh = snap.get("macd_hist")
    mh_prev = snap.get("macd_hist_prev")
    pos = snap.get("pos_52")
    return {
        "above_ma50": px is not None and ma50 is not None and px >= ma50,
        "above_ma200": px is not None and ma200 is not None and px >= ma200,
        "below_ma200": px is not None and ma200 is not None and px < ma200,
        "rsi_hot": rsi_v is not None and rsi_v >= 70,
        "rsi_cold": rsi_v is not None and rsi_v <= 30,
        "macd_up": mh is not None and mh_prev is not None and mh > 0 and mh >= mh_prev,
        "macd_down": mh is not None and mh_prev is not None and mh < 0 and mh <= mh_prev,
        "pos_high": pos is not None and pos >= 85,
        "pos_low": pos is not None and pos <= 25,
    }


# 直接、可执行的动作词表（对应 spec 里的 8 类结论）
def _suggestion(role: str, t: dict[str, bool], persona: str) -> tuple[str, str]:
    """返回 (动作标签, 一句话理由)。persona ∈ {conservative, balanced, aggressive}。"""

    if role == "core":
        if t["pos_high"] and t["rsi_hot"]:
            if persona == "aggressive":
                return "Normal DCA", "已接近区间高位，按计划定投即可，不必额外追高。"
            return "Slower DCA", "位置偏高、RSI 偏热，放慢定投节奏、别一次买满。"
        if t["below_ma200"]:
            return "Add on weakness", "跌破 200 日均线但宏观逻辑未破坏，可分批加一点点。"
        return "Normal DCA", "宽基核心仓，正常长期定投、无需择时。"

    if role == "mega":
        if t["rsi_hot"] and t["pos_high"]:
            return "Do not chase", "涨得又快又高，短期别追，等回踩再看。"
        if not t["above_ma50"] and t["above_ma200"]:
            if persona == "conservative":
                return "Wait for confirmation", "回落到 50 日线下方，先等企稳，别急着接。"
            return "Add on weakness", "回调但仍在 200 日线上方，基本面没变可小额分批。"
        if t["below_ma200"]:
            return "Wait for confirmation", "已跌破 200 日线，先确认是不是基本面转弱。"
        return "Hold", "趋势健康、无异常，继续持有。"

    if role == "satellite":
        if t["pos_high"] or t["rsi_hot"]:
            return "Do not chase", "高波动卫星仓已冲高，别追；跌了也不代表便宜。"
        if t["below_ma200"]:
            if persona == "aggressive":
                return "Wait for confirmation", "跌破 200 日线，等放量企稳信号，不做无脑抄底。"
            return "Avoid for now", "高波动 + 跌破 200 日线，先避开，等基本面/现金流看清。"
        return "Hold", "先拿着看订单/现金流，不因单日波动加减仓。"

    # other / watch
    if t["rsi_hot"] and t["pos_high"]:
        return "Do not chase", "偏热偏高，观察为主。"
    return "Hold", "无明显信号，跟随大盘。"


def _fact_line(snap: dict[str, Any]) -> str:
    sym = snap["symbol"]
    px = snap.get("px")
    chg = snap.get("chg_pct") or 0.0
    rsi_v = snap.get("rsi")
    pos = snap.get("pos_52")
    ma200 = snap.get("ma200")
    trend = "站上200日线" if (px is not None and ma200 is not None and px >= ma200) else "在200日线下方"
    rsi_s = f"RSI {rsi_v:.0f}" if rsi_v is not None else "RSI —"
    pos_s = f"52周位置 {pos:.0f}%" if pos is not None else ""
    return f"{sym} ${px:,.2f}（{chg:+.2f}%）· {trend} · {rsi_s} · {pos_s}".strip(" ·")


_PERSONA_META = {
    "conservative": ("🛡️ 稳健派", "先保住本金，宽基优先，宁可错过不可做错。"),
    "balanced": ("⚖️ 均衡派", "核心 + 卫星搭配，基本面没变才在弱势里小额分批。"),
    "aggressive": ("🚀 激进派", "看重动能和趋势，愿意在强势里加仓，但严格控制单只仓位。"),
}


def _persona_paragraph(persona: str, snaps: list[dict[str, Any]]) -> str:
    title, stance = _PERSONA_META[persona]
    lines = [f"**{title}** — {stance}", ""]
    for snap in snaps:
        t = _tags(snap)
        action, reason = _suggestion(snap["role"], t, persona)
        lines.append(f"- **{snap['symbol']}** → `{action}`：{reason}")
    return "\n".join(lines)


def verdict_for_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    """单一「Willow 结论」:一个动作 + 理由 + 下一步该盯什么。复用规则标签。"""

    t = _tags(snap)
    action, reason = _suggestion(snap.get("role", "other"), t, "balanced")
    if t["below_ma200"]:
        watch = "看能否重新站上 200 日线;先确认不是基本面转弱"
    elif t["rsi_hot"] or t["pos_high"]:
        watch = "等回踩到 MA50 附近、且没有财报/监管坏消息再看"
    elif not t["above_ma50"]:
        watch = "看能否站稳 MA50"
    else:
        watch = "跟随大盘,暂无特别价位"
    return {"action": action, "reason": reason, "watch_next": watch, "tags": t}


def generate_commentary(symbols: list[str]) -> dict[str, Any]:
    """三位顾问对持仓的规则化点评 + 指标快照表。"""

    snaps: list[dict[str, Any]] = []
    for sym in symbols:
        snap = symbol_indicator_snapshot(sym)
        if snap:
            snaps.append(snap)

    # 按当日涨跌幅绝对值排序，先看真正在动的
    ordered = sorted(snaps, key=lambda s: abs(float(s.get("chg_pct") or 0.0)), reverse=True)

    personas = {
        p: _persona_paragraph(p, ordered) for p in ("conservative", "balanced", "aggressive")
    }
    return {
        "snapshots": ordered,
        "facts": [_fact_line(s) for s in ordered],
        "personas": personas,
        "source": "rule_based",
    }


def llm_enhance(commentary: dict[str, Any]) -> dict[str, Any]:
    """可选：用现有 LLM 路由把规则化点评改写得更自然（需已配置 key）。失败则原样返回。"""

    try:
        from llm.router import TokenGuard  # noqa: PLC0415

        facts = "\n".join(commentary.get("facts") or [])
        base = "\n\n".join(commentary.get("personas", {}).values())
        prompt = (
            "你是面向股票新手的中文投资助手。以下是用户持仓的客观指标事实和三位顾问"
            "（稳健/均衡/激进）的初步结论。请用简单大白话、口语化地重写这三段点评，"
            "保留每只股票的动作标签（如 Normal DCA / Hold / Do not chase），不要新增买卖承诺，"
            "不要免责声明堆砌。\n\n"
            f"指标事实:\n{facts}\n\n初步结论:\n{base}"
        )
        out = TokenGuard().call("holding_commentary", prompt)
        txt = (out or {}).get("text") or ""
        if txt.strip():
            commentary = dict(commentary)
            commentary["llm_text"] = txt.strip()
            commentary["source"] = f"llm:{out.get('routed')}"
    except Exception:
        pass
    return commentary
