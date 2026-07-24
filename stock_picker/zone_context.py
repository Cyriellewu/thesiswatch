"""Explainable zone metadata: ETF vs 个股、区间可信度、数据质量（不写库）。"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from signals.indicators import load_daily_bars

_ROOT = Path(__file__).resolve().parents[1]


def _settings() -> dict[str, Any]:
    p = _ROOT / "config" / "settings.yaml"
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def etf_weekly_dca_usd(symbol: str) -> float | None:
    raw = _settings().get("etf_weekly_dca_usd") or {}
    v = raw.get(str(symbol).upper())
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def instrument_kind(symbol: str, bucket: str, stock_type: str) -> str:
    s = str(symbol).upper()
    if s in {"VOO", "VTI", "SPY", "IVV", "QQQ", "QQQM", "SCHD"} or str(bucket) == "ETF / 大盘底仓":
        return "etf"
    if str(bucket) == "高波动观察" or stock_type == "high_vol_watch":
        return "high_vol"
    return "steady"


@lru_cache(maxsize=256)
def _atr_pct(symbol: str) -> float | None:
    h = load_daily_bars(symbol, period="6mo")
    if h is None or len(h) < 30 or not {"High", "Low", "Close"}.issubset(h.columns):
        return None
    high = h["High"].astype(float)
    low = h["Low"].astype(float)
    close = h["Close"].astype(float)
    prev = close.shift(1)
    tr = (high - low).combine((high - prev).abs(), max).combine((low - prev).abs(), max)
    atr = tr.tail(20).mean()
    last = float(close.iloc[-1])
    if last <= 0 or atr is None or (isinstance(atr, float) and math.isnan(atr)):
        return None
    return float(atr) / last * 100.0


@lru_cache(maxsize=256)
def bar_count(symbol: str) -> int:
    h = load_daily_bars(symbol, period="5y")
    return len(h) if h is not None else 0


def zone_width_pct(rec_lo: float, rec_hi: float, px: float) -> float:
    if px <= 0 or rec_hi <= 0:
        return 1.0
    return max(0.0, (rec_hi - rec_lo) / px)


def zone_confidence(
    *,
    symbol: str,
    row: Any,
    hist: dict[str, float],
    rec_lo: float,
    rec_hi: float,
    px: float,
) -> tuple[int, str, str]:
    """0–10 score → 高/中/低 + 一句依据。"""
    score = 0
    parts: list[str] = []
    n = bar_count(symbol)
    if n >= 250:
        score += 2
        parts.append("日线≥250")
    elif n >= 120:
        score += 1
        parts.append("日线≥120")
    else:
        parts.append("历史偏短")

    ma200 = float(getattr(row, "ma200", 0.0) or 0.0)
    if ma200 > 0:
        score += 2
        parts.append("MA200 可用")

    t = int(getattr(row, "t_score", 0) or 0)
    if t >= 4:
        score += 2
        parts.append(f"T={t}")
    elif t >= 2:
        score += 1
        parts.append(f"T={t}")
    else:
        parts.append(f"T={t}（偏弱）")

    atrp = _atr_pct(symbol)
    kind = instrument_kind(symbol, str(getattr(row, "bucket", "")), str(getattr(row, "stock_type", "")))
    if atrp is not None and atrp < 6.0:
        score += 1
        parts.append(f"ATR%≈{atrp:.1f}")
    elif kind == "high_vol" and atrp is not None:
        parts.append(f"高波动 ATR%≈{atrp:.1f}")

    zw = zone_width_pct(rec_lo, rec_hi, px)
    if zw < 0.25:
        score += 1
        parts.append("区间宽度适中")
    elif zw > 0.35:
        parts.append("区间偏宽")

    if str(symbol).upper().endswith((".KS", ".JP", ".T", ".HK")):
        score = max(0, score - 4)
        parts.append("非美股货币/规则不同：区间仅供参考")

    if px > 50_000:
        score = min(score, 2)
        parts.append("价格异常高：疑似币种/单位问题")

    if abs(float(hist.get("ret_5y", 0.0))) < 1e-6 and n < 60:
        score = min(score, 4)
        parts.append("5Y 收益不可用或数据不足")

    label = "低" if score <= 4 else ("中" if score <= 7 else "高")
    return score, label, " · ".join(parts[:5])


def etf_extra_band(ma200: float) -> dict[str, float]:
    m = max(0.01, float(ma200))
    return {
        "extra_low": round(m * 0.90, 2),
        "extra_high": round(m * 1.03, 2),
        "comfort_pullback": round(m * 0.97, 2),
        "deep_pullback": round(m * 0.90, 2),
        "do_not_chase": round(m * 1.12, 2),
    }


def zone_basis_line(row: Any) -> str:
    kind = instrument_kind(str(row.symbol), str(row.bucket), str(getattr(row, "stock_type", "")))
    if kind == "etf":
        return "区间依据：ETF 额外加仓带 = MA200×[0.90, 1.03]；定投金额见下方（与「等回踩」无关）。"
    if kind == "high_vol":
        return "区间依据：高波动股使用 typed_entry_zones（ATR/回撤权重）；勿与稳健蓝筹同一套直觉。"
    return "区间依据：typed_entry_zones（MA/回撤/波动类型）；结合 Q/T/P/C 与当前动作标签。"


def format_etf_dca_block(symbol: str, px: float, ma200: float) -> str:
    usd = etf_weekly_dca_usd(symbol)
    if usd is None:
        return ""
    b = etf_extra_band(ma200)
    in_extra = b["extra_low"] <= px <= b["extra_high"]
    extra_state = "已进入额外加仓带" if in_extra else "未触发额外加仓"
    return (
        f"**每周定投**：继续约 **${usd:,.0f}/周**（与价格区间无关）。  \n"
        f"**额外加仓带**：${b['extra_low']:,.2f} – ${b['extra_high']:,.2f}（相对 MA200）。  \n"
        f"**当前价** ${px:,.2f} · **{extra_state}**（偏高则只定投、不额外追）。  \n"
        f"参考：更舒适回踩 ≈ ${b['comfort_pullback']:,.2f} · 不追参考 > ${b['do_not_chase']:,.2f}  \n"
        f"**提醒**：当价格进入 **${b['extra_low']:,.2f}–${b['extra_high']:,.2f}** 带内，本应用会尝试写入 `ENTER_EXTRA_DCA_ZONE`（`book_system` alerts，冷却去重）。"
    )


def zone_label_for_trend(row: Any) -> str:
    """趋势过弱时不叫「推荐区间」，改称观察区间。"""
    t = int(getattr(row, "t_score", 0) or 0)
    if t < 4:
        return "观察区间"
    return "推荐区间"
