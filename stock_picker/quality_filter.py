from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from data_layer.market_data import fetch_quotes
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from db.client import get_conn
from monitors.market_brake import load_last_market_brake_snapshot
from stock_picker.cache_store import load_weekly_blurb, save_weekly_blurb
from stock_picker import entry_zone
from stock_picker.sector_classifier import classify_sector

ROOT = Path(__file__).resolve().parents[1]


def _safe_calc_typed_entry_zones(
    *,
    current_px: float,
    low_3m: float,
    low_6m: float,
    high_3m: float,
    high_52w: float,
    ma50: float,
    ma100: float,
    ma200: float,
    annualized_volatility: float,
    stock_type: str,
):
    """
    Backward-compatible fallback for environments where `entry_zone`
    is stale and does not expose `calc_typed_entry_zones`.
    """
    global entry_zone
    fn = getattr(entry_zone, "calc_typed_entry_zones", None)
    if not callable(fn):
        try:
            entry_zone = importlib.reload(entry_zone)
            fn = getattr(entry_zone, "calc_typed_entry_zones", None)
        except Exception:
            fn = None
    if callable(fn):
        return fn(
            current_px=current_px,
            low_3m=low_3m,
            low_6m=low_6m,
            high_3m=high_3m,
            high_52w=high_52w,
            ma50=ma50,
            ma100=ma100,
            ma200=ma200,
            annualized_volatility=annualized_volatility,
            stock_type=stock_type,
        )
    # Fallback to simpler legacy ladder.
    legacy = entry_zone.calc_entry_zones(
        current_px=current_px,
        low_3m=low_3m,
        low_6m=low_6m,
        ma50=ma50,
        ma200=ma200,
    )
    # Patch fields expected by current UI if older dataclass lacks them.
    if not hasattr(legacy, "stock_type"):
        setattr(legacy, "stock_type", stock_type)
    if not hasattr(legacy, "trend_warning"):
        setattr(legacy, "trend_warning", bool(ma200 and current_px < ma200 * 0.95))
    if not hasattr(legacy, "status"):
        status = "当前可分批" if legacy.recommended_low <= current_px <= legacy.recommended_high else "等回推荐区间"
        setattr(legacy, "status", status)
    if not hasattr(legacy, "comfortable_buy"):
        setattr(legacy, "comfortable_buy", round(min(float(legacy.recommended_low), float(current_px) * 0.97), 2))
    return legacy


def _safe_calc_exit_zones(*, current_px: float, ma50: float, ma200: float, avoid_above: float):
    """
    Backward-compatible fallback:
    some local environments may still have an older `stock_picker.entry_zone`
    module loaded without `calc_exit_zones`.
    """
    global entry_zone
    fn = getattr(entry_zone, "calc_exit_zones", None)
    if not callable(fn):
        try:
            entry_zone = importlib.reload(entry_zone)
            fn = getattr(entry_zone, "calc_exit_zones", None)
        except Exception:
            fn = None
    if callable(fn):
        return fn(current_px=current_px, ma50=ma50, ma200=ma200, avoid_above=avoid_above)
    # Minimal fallback object with expected fields.
    class _Exit:
        trim_watch = round(max(float(current_px) * 1.04, float(avoid_above) * 0.95), 2)
        pullback_risk = round(max(float(avoid_above), float(current_px) * 1.08), 2)
        trend_break = round(min(float(current_px) * 0.92, float(ma50) * 0.97), 2)

    return _Exit()


@dataclass
class PickRow:
    symbol: str
    sector: str
    stars: int
    current_px: float
    chg_pct: float
    zones: entry_zone.EntryZones
    exits: object
    reason: str
    risk: str


@dataclass
class HistMetrics:
    low_3m: float
    low_6m: float
    ma20: float
    ma50: float
    ma100: float
    ma200: float
    ret_1w: float
    ret_1m: float
    ret_3m: float
    ret_1y: float
    high_52w: float
    high_3m: float
    vol_1m: float


@dataclass
class OpportunityRow:
    symbol: str
    name: str
    sector: str
    bucket: str
    source: str
    price: float
    chg_pct: float
    ret_1w: float
    ret_1m: float
    ret_3m: float
    q_score: int
    t_score: int
    p_score: int
    p_status: str
    c_score: int
    label: str
    advice: str
    priority_score: float
    alert_suggestions: list[str]
    zones: entry_zone.EntryZones
    exits: object
    reason_short: str
    reason_detail: str
    risk_short: str
    hover_explanation: str
    bucket_gap_note: str
    news_score: int = 0
    news_evidence: list[dict[str, Any]] | None = None
    decision_queue: str = "小仓观察"
    is_buyable_now: bool = False
    buyable_tier: str = ""
    buyable_reason: str = ""
    buy_mode: str = "wait_pullback"
    current_action: str = "observe"
    zone_position: float = 1.0
    ma50: float = 0.0
    ma200: float = 0.0
    high_52w: float = 0.0


def _load_yaml(name: str) -> dict[str, Any]:
    path = ROOT / "config" / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _bucket_config() -> dict[str, Any]:
    return _load_yaml("opportunity_buckets.yaml")


def _symbol_metadata() -> dict[str, dict[str, Any]]:
    raw = _load_yaml("symbol_metadata.yaml")
    return {str(k).upper(): dict(v or {}) for k, v in raw.items()}


def _bucket_map() -> tuple[dict[str, str], dict[str, str]]:
    cfg = _bucket_config()
    sym_to_bucket: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for bucket, body in (cfg.get("buckets") or {}).items():
        descriptions[str(bucket)] = str((body or {}).get("description") or "")
        for sym in (body or {}).get("symbols") or []:
            sym_to_bucket[str(sym).upper()] = str(bucket)
    return sym_to_bucket, descriptions


def bucket_descriptions() -> dict[str, str]:
    return _bucket_map()[1]


def _universe_with_source() -> dict[str, str]:
    out: dict[str, str] = {}
    for sym in load_watchlist_tickers():
        out[str(sym).upper()] = "user"
    for sym in load_opportunity_tickers():
        out.setdefault(str(sym).upper(), "revaluation")
    cfg = _bucket_config()
    for body in (cfg.get("buckets") or {}).values():
        for sym in (body or {}).get("symbols") or []:
            out.setdefault(str(sym).upper(), "core")
    return out


def _universe() -> list[str]:
    return list(_universe_with_source().keys())


@lru_cache(maxsize=256)
def _hist_metrics(sym: str) -> HistMetrics | None:
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return None
    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception:
        return None
    try:
        h = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=True)
    except Exception:
        return None
    if h is None or h.empty or len(h.index) < 40:
        return None
    close = h["Close"].astype(float)
    last = float(close.iloc[-1])

    def ret(days: int) -> float:
        if len(close) <= days:
            return 0.0
        old = float(close.iloc[-days - 1])
        return ((last - old) / old * 100.0) if old else 0.0

    ma20 = float(close.tail(20).mean())
    ma50 = float(close.tail(50).mean()) if len(close) >= 50 else float(close.mean())
    ma100 = float(close.tail(100).mean()) if len(close) >= 100 else ma50
    ma200 = float(close.tail(200).mean()) if len(close) >= 200 else float(close.mean())
    ret_daily = close.pct_change().dropna().tail(252)
    vol = float(ret_daily.std() * 100.0) if len(ret_daily) else 0.0
    return HistMetrics(
        low_3m=float(close.tail(90).min()) if len(close) >= 90 else float(close.min()),
        low_6m=float(close.tail(180).min()) if len(close) >= 180 else float(close.min()),
        ma20=ma20,
        ma50=ma50,
        ma100=ma100,
        ma200=ma200,
        ret_1w=ret(5),
        ret_1m=ret(21),
        ret_3m=ret(63),
        ret_1y=ret(252) if len(close) > 252 else ret(len(close) - 1),
        high_52w=float(close.max()),
        high_3m=float(close.tail(63).max()) if len(close) >= 63 else float(close.max()),
        vol_1m=vol,
    )


def _fallback_hist(px: float) -> HistMetrics:
    return HistMetrics(
        low_3m=px * 0.92,
        low_6m=px * 0.86,
        ma20=px * 0.99,
        ma50=px * 0.98,
        ma100=px * 0.96,
        ma200=px * 0.94,
        ret_1w=0.0,
        ret_1m=0.0,
        ret_3m=0.0,
        ret_1y=0.0,
        high_52w=px * 1.08,
        high_3m=px * 1.06,
        vol_1m=25.0,
    )


def _bucket_for(sym: str, meta: dict[str, Any], sym_to_bucket: dict[str, str]) -> str:
    bucket = str(meta.get("bucket") or sym_to_bucket.get(sym) or classify_sector(sym) or "其它")
    if bucket in {"Offline", "Unknown", "unknown", "None", ""}:
        return "其它"
    return bucket


def _is_broad_etf(sym: str) -> bool:
    return sym in {"VOO", "VTI", "SPY", "IVV", "QQQ", "QQQM", "SCHD"}


def _stock_type(sym: str, bucket: str, meta: dict[str, Any]) -> str:
    meta_type = str(meta.get("type") or "")
    if sym in {"VOO", "VTI", "SPY", "IVV", "QQQM", "SCHD"}:
        return "etf_core"
    if bucket in {"消费防御", "医疗健康", "金融 / 支付"} or sym in {"KO", "PG", "JNJ", "WMT", "COST", "V", "MA", "PEP", "MCD"}:
        return "defensive_quality"
    if bucket == "高波动观察" or "高波动" in meta_type or sym in {"IREN", "BE", "OKLO", "SMR", "APLD", "CORZ"}:
        return "high_vol_watch"
    if "重估" in meta_type or bucket in {"工业 / 基建 / 电力", "能源 / 公用事业"} or sym in {"SNDK", "MU", "WDC", "CEG", "VST", "VRT"}:
        return "revaluation_trend"
    if bucket == "科技 / AI / 半导体" or sym in {"NVDA", "AVGO", "AMD", "TSM", "ASML"}:
        return "ai_growth"
    return "steady_growth"


_REVALUATION_HINTS = {
    "spinoff",
    "spin-off",
    "separation",
    "index inclusion",
    "s&p 500 inclusion",
    "ai demand",
    "data center",
    "datacenter",
    "electricity demand",
    "power demand",
    "supply shortage",
    "cycle recovery",
    "guidance raise",
    "raises guidance",
    "margin expansion",
}

_NEGATIVE_HINTS = {
    "plunge",
    "plunged",
    "falls",
    "fell",
    "drop",
    "dropped",
    "miss",
    "misses",
    "cut",
    "cuts",
    "downgrade",
    "lawsuit",
    "probe",
    "investigation",
    "warning",
}

_POSITIVE_HINTS = {
    "jump",
    "jumped",
    "surge",
    "surged",
    "beat",
    "beats",
    "upgrade",
    "raise",
    "raises",
    "partnership",
    "deal",
    "record",
}


def _parse_affected(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x).upper() for x in raw if x]
    try:
        data = json.loads(str(raw or "[]"))
        if isinstance(data, list):
            return [str(x).upper() for x in data if x]
    except Exception:
        pass
    return []


def _load_recent_news_evidence(symbols: list[str], hours: int = 72) -> dict[str, list[dict[str, Any]]]:
    """Small, rule-first evidence loader for opportunity scoring.

    This intentionally does not call an LLM. It only pulls already-fetched news
    that directly names a candidate so news can nudge rank/C-score and card copy.
    """

    if not symbols:
        return {}
    wanted = {s.upper() for s in symbols}
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in wanted}
    try:
        conn = get_conn(read_only=True)
    except Exception:
        return out
    try:
        rows = conn.execute(
            """
            SELECT id, title, COALESCE(one_line_zh,'') AS one_line_zh,
                   COALESCE(impact_on_holdings,'') AS impact_on_holdings,
                   COALESCE(source,'') AS source, COALESCE(url,'') AS url,
                   published_at, COALESCE(primary_ticker,'') AS primary_ticker,
                   COALESCE(affected_tickers,'[]') AS affected_tickers,
                   COALESCE(category,'') AS category, COALESCE(severity,'') AS severity
            FROM news
            WHERE published_at >= datetime('now', ?)
            ORDER BY published_at DESC
            LIMIT 800
            """,
            (f"-{int(hours)} hours",),
        ).fetchall()
    except Exception:
        return out
    finally:
        conn.close()

    for row in rows:
        primary = str(row["primary_ticker"] or "").upper()
        affected = set(_parse_affected(row["affected_tickers"]))
        candidates = ({primary} | affected) & wanted
        if not candidates:
            continue
        title = str(row["title"] or "")
        one_line = str(row["one_line_zh"] or "")
        text = f"{title} {one_line} {row['category']}".lower()
        for sym in candidates:
            evidence = {
                "id": row["id"],
                "title": title,
                "one_line_zh": one_line,
                "impact_on_holdings": row["impact_on_holdings"],
                "source": row["source"],
                "url": row["url"],
                "published_at": row["published_at"],
                "category": row["category"],
                "severity": row["severity"],
                "is_revaluation": any(k in text for k in _REVALUATION_HINTS),
                "is_negative": any(k in text for k in _NEGATIVE_HINTS),
                "is_positive": any(k in text for k in _POSITIVE_HINTS),
            }
            out.setdefault(sym, []).append(evidence)
    return {k: v[:5] for k, v in out.items()}


def _news_score(items: list[dict[str, Any]]) -> int:
    score = 0
    for item in items[:4]:
        sev = str(item.get("severity") or "")
        cat = str(item.get("category") or "")
        if sev == "urgent":
            score += 4
        elif sev == "important":
            score += 3
        elif sev == "attention":
            score += 1
        if item.get("is_revaluation"):
            score += 2
        if cat in {"earnings", "rating"}:
            score += 2
        if item.get("is_positive"):
            score += 1
        if item.get("is_negative"):
            score -= 3
    return max(-5, min(8, int(score)))


def _quality_score(sym: str, bucket: str, hist: HistMetrics, meta: dict[str, Any]) -> int:
    if _is_broad_etf(sym):
        return 10
    score = 4
    if bucket in {"消费防御", "医疗健康", "金融 / 支付", "工业 / 基建 / 电力", "能源 / 公用事业"}:
        score += 2
    if bucket in {"科技 / AI / 半导体", "通信服务"}:
        score += 1
    if meta.get("quality_note"):
        score += 2
    if hist.ret_1y > 0:
        score += 1
    if hist.ma50 >= hist.ma200 * 0.98:
        score += 1
    if hist.vol_1m > 5 and bucket == "高波动观察":
        score -= 1
    if bucket in {"Unknown", "其它"}:
        score -= 1
    return max(1, min(10, int(score)))


def _trend_score(px: float, chg: float, hist: HistMetrics, spy_ret_1m: float, spy_ret_3m: float) -> int:
    score = 0
    if hist.ma50 > hist.ma200:
        score += 2
    if px > hist.ma200:
        score += 1
    if hist.ma20 >= hist.ma50 * 0.99:
        score += 1
    if hist.ret_1m > spy_ret_1m:
        score += 1
    if hist.ret_3m > spy_ret_3m:
        score += 1
    if hist.ret_1m > 8:
        score += 1
    if hist.ret_3m > 20:
        score += 1
    if abs(chg) > 6 or hist.vol_1m > 6:
        score -= 1
    return max(1, min(10, int(score)))


def _position_status(px: float, zones: entry_zone.EntryZones) -> tuple[str, int]:
    if px <= zones.deep_value:
        return "低于推荐区间", 9
    if zones.recommended_low <= px <= zones.recommended_high:
        return "合理区间", 8
    if px < zones.avoid_above:
        return "偏高", 5
    return "过热", 2


def _catalyst_score(sym: str, bucket: str, hist: HistMetrics, meta: dict[str, Any], news_items: list[dict[str, Any]] | None = None) -> int:
    blob = " ".join(str(meta.get(k) or "") for k in ("type", "quality_note", "revaluation_note", "theme")).lower()
    score = 0
    if hist.ret_1m > 15:
        score += 1
    if hist.ret_3m > 30:
        score += 1
    if hist.high_52w and hist.high_52w > 0 and hist.high_52w * 0.92 <= max(hist.ma20, hist.ma50):
        score += 1
    keywords = []
    for body in (_bucket_config().get("revaluation_themes") or {}).values():
        keywords.extend(body.get("keywords") or [])
    if any(str(k).lower() in blob for k in keywords):
        score += 2
    if bucket in {"工业 / 基建 / 电力", "科技 / AI / 半导体", "高波动观察"}:
        score += 1
    if "重估" in str(meta.get("type") or ""):
        score += 2
    if news_items:
        if any(n.get("is_revaluation") for n in news_items):
            score += 2
        if any(str(n.get("category") or "") in {"earnings", "rating"} for n in news_items):
            score += 1
    return max(0, min(10, score))


def _classify_label(q: int, t: int, p_status: str, c: int, sym: str, meta: dict[str, Any]) -> str:
    if q < 4:
        return "质量存疑"
    if _is_broad_etf(sym):
        return "长期底仓"
    if c >= 5 and q >= 7 and t >= 6:
        return "重估趋势"
    if q >= 8 and t >= 7:
        return "高质量强趋势"
    if q >= 8 and t >= 4:
        return "高质量慢牛"
    if p_status in {"偏高", "过热"} and t >= 6:
        return "偏高等回踩"
    if q >= 6 and t >= 6:
        return "波动机会"
    if t < 3:
        return "趋势一般"
    return str(meta.get("type") or "观察候选")


def _advice(q: int, t: int, p_status: str, sym: str, stock_type: str, chg_pct: float, px: float, hist: HistMetrics) -> str:
    if q < 4:
        return "暂不看"
    if stock_type in {"revaluation_trend", "high_vol_watch"}:
        if chg_pct > 10:
            return "重大异动，不追"
        if hist.ma200 > 0 and px < hist.ma200 * 0.95:
            return "便宜但趋势弱"
        if hist.ma50 > 0 and px > hist.ma50 * 1.10:
            return "偏高不追"
    if _is_broad_etf(sym):
        return "只适合定投"
    if p_status == "合理区间" and q >= 7 and t >= 4:
        return "可分批"
    if p_status == "低于推荐区间" and t >= 5 and q >= 6:
        return "可重点关注"
    if p_status == "低于推荐区间" and t < 4:
        return "便宜但趋势弱"
    if p_status == "偏高" and t >= 6:
        return "等回踩"
    if p_status == "过热":
        return "偏高不追"
    return "小仓观察"


def _alert_suggestions(label: str, advice: str, zones: entry_zone.EntryZones, hist: HistMetrics) -> list[str]:
    suggestions: list[str] = []
    if advice in {"可分批", "可重点关注"}:
        suggestions += [
            f"跌到推荐区间下沿 ${zones.recommended_low:,.2f}",
            f"更舒服回踩 ${zones.comfortable_buy:,.2f}",
            "单日下跌 3%",
            "从近期高点回撤 8%",
        ]
    elif advice == "等回踩":
        suggestions += [
            f"跌入推荐区间 ${zones.recommended_low:,.2f}-{zones.recommended_high:,.2f}",
            f"跌到更舒服回踩 ${zones.comfortable_buy:,.2f}",
            "从近期高点回撤 10%",
        ]
    elif advice == "偏高不追":
        suggestions += [
            f"从过热区回落到不追位以下 ${zones.avoid_above:,.2f} 时复查",
            f"回到推荐区间 ${zones.recommended_high:,.2f}",
            "从高点回撤 12%",
        ]
    elif advice == "只适合定投":
        suggestions += ["月度定投提醒", f"跌入推荐区间 ${zones.recommended_low:,.2f}", f"跌到更舒服回踩 ${zones.comfortable_buy:,.2f}"]
    else:
        suggestions += [f"跌入推荐区间 ${zones.recommended_low:,.2f}", "单日上涨 5%", "单日下跌 4%"]
    if label == "重估趋势":
        suggestions += ["单日上涨 5%", "3 日累计上涨 8%", "跌破 20 日均线"]
    return list(dict.fromkeys(suggestions))[:6]


def portfolio_bucket_weights(rows: list[OpportunityRow] | None = None) -> dict[str, float]:
    from data_layer.portfolio_analytics import load_positions

    positions = load_positions()
    if not positions:
        return {}
    sym_to_bucket, _ = _bucket_map()
    meta = _symbol_metadata()
    qmap = {q.symbol.upper(): q for q in fetch_quotes([p.ticker for p in positions])}
    totals: dict[str, float] = {}
    total = 0.0
    for p in positions:
        sym = p.ticker.upper()
        px = float(qmap.get(sym).px if qmap.get(sym) else p.avg_cost_per_share)
        mv = float(p.qty) * px
        bucket = _bucket_for(sym, meta.get(sym, {}), sym_to_bucket)
        totals[bucket] = totals.get(bucket, 0.0) + mv
        total += mv
    return {k: (v / total * 100.0 if total else 0.0) for k, v in totals.items()}


def _portfolio_gap_score(bucket: str, weights: dict[str, float]) -> tuple[float, str]:
    w = float(weights.get(bucket, 0.0))
    if bucket == "科技 / AI / 半导体" and w >= 45:
        return -2.0, f"{bucket} 已约 {w:.0f}%：偏集中，降低新加仓优先级"
    if bucket in {"消费防御", "医疗健康", "金融 / 支付", "工业 / 基建 / 电力", "能源 / 公用事业"} and w < 8:
        return 2.0, f"{bucket} 约 {w:.0f}%：低配，适合补一点"
    if bucket == "ETF / 大盘底仓" and w < 20:
        return 1.0, f"{bucket} 约 {w:.0f}%：可作为底仓"
    return 0.0, f"{bucket} 约 {w:.0f}%"


def _market_overheat_penalty(bucket: str) -> float:
    if bucket not in {"科技 / AI / 半导体", "通信服务", "高波动观察"}:
        return 0.0
    try:
        snap = load_last_market_brake_snapshot()
    except Exception:
        return 0.0
    if not snap:
        return 0.0
    if snap.status.startswith("🔴"):
        return 1.0
    if snap.status.startswith("🟠"):
        return 0.5
    return 0.0


def _decision_queue(
    *,
    advice: str,
    p_status: str,
    q_score: int,
    t_score: int,
    c_score: int,
    bucket: str,
    bucket_weight: float,
) -> str:
    if advice in {"可分批", "可重点关注"} and p_status in {"合理区间", "低于推荐区间"} and q_score >= 7:
        if bucket == "科技 / AI / 半导体" and bucket_weight >= 45:
            return "好票等回踩"
        return "现在可入场"
    if q_score >= 7 and (t_score >= 5 or c_score >= 3) and p_status in {"偏高", "过热"}:
        return "好票等回踩"
    if q_score >= 6 and advice not in {"暂不看", "偏高不追"}:
        return "小仓观察"
    return "暂不看"


def _zone_position(px: float, zones: entry_zone.EntryZones) -> float:
    width = float(zones.recommended_high) - float(zones.recommended_low)
    if width <= 0:
        return 1.0
    return (float(px) - float(zones.recommended_low)) / width


def _queue_from_action(action: str) -> str:
    if action in {"priority_buyable", "buyable"}:
        return "现在可分批"
    if action in {"wait_pullback", "dca_only"}:
        return "好票等回踩"
    if action == "do_not_touch":
        return "暂不看"
    return "小仓观察"


def _advice_from_action(action: str, fallback: str = "") -> str:
    """Single display advice derived from the final decision action.

    Raw price position is only one input. The UI should never show
    "可分批" while the final action says "等回踩" or "只适合定投".
    """
    mapping = {
        "priority_buyable": "可分批",
        "buyable": "可分批",
        "cautious_buyable": "谨慎小仓",
        "wait_pullback": "等回踩",
        "dca_only": "只适合定投",
        "momentum_alert_only": "重大异动，不追",
        "avoid_chase": "偏高不追",
        "observe": "小仓观察",
        "do_not_touch": "暂不看",
    }
    return mapping.get(action, fallback or "小仓观察")


def _buyable_now_state(
    *,
    sym: str,
    stock_type: str,
    px: float,
    chg_pct: float,
    zones: entry_zone.EntryZones,
    q_score: int,
    t_score: int,
    bucket: str,
    gap_score: float,
    news_items: list[dict[str, Any]],
) -> tuple[bool, str, str]:
    in_range = zones.recommended_low <= px <= zones.recommended_high
    pos = _zone_position(px, zones)
    severe_negative = any(
        item.get("is_negative") and str(item.get("severity") or "") in {"urgent", "important"}
        for item in news_items[:5]
    )
    is_etf = _is_broad_etf(sym)
    if stock_type in {"revaluation_trend", "high_vol_watch"} and chg_pct > 8:
        return False, "", "重大异动后不追，先设回踩提醒。"
    if is_etf and sym in {"QQQ", "QQQM", "XLK"} and (gap_score < 0 or chg_pct > 1.5):
        return False, "dca_only", "科技成长 ETF 可长期定投，但科技仓已重或当天涨幅较大，不算额外加仓点。"
    if severe_negative:
        return False, "", "有重大负面新闻，先检查原因。"
    if not in_range:
        return False, "", "尚未进入推荐区间。"

    # A card can be in the price zone but still not be a "now buy" action.
    # Weak trend or being near the upper edge becomes cautious observe.
    if t_score < 4 and not is_etf:
        return False, "cautious_buyable", "价格在区间，但趋势分低于 4，只适合观察或很小仓。"
    if is_etf and t_score < 3:
        return False, "dca_only", "ETF 可定投，但趋势不足，不是额外加仓点。"
    if q_score < (6 if is_etf else 7):
        return False, "", "价格在区间，但质量或趋势不足。"
    if pos >= 0.70:
        return False, "cautious_buyable", "价格在推荐区间内但接近上沿，谨慎小仓，优先等更舒服回踩。"

    if gap_score > 0 and q_score >= 8 and pos <= 0.35:
        return True, "priority_buyable", "低配板块 + 质量高 + 已在推荐区间，优先级更高。"
    return True, "normal_buyable", "质量/趋势通过，且当前位于推荐区间。"


def _buy_mode(
    *,
    sym: str,
    stock_type: str,
    px: float,
    chg_pct: float,
    zones: entry_zone.EntryZones,
    is_buyable_now: bool,
    bucket: str,
    gap_score: float,
    hist: HistMetrics,
) -> str:
    if _is_broad_etf(sym):
        tech_etf = sym in {"QQQ", "QQQM", "XLK"} or bucket == "科技 / AI / 半导体"
        if tech_etf and (gap_score < 0 or chg_pct > 1.5 or (hist.ma50 > 0 and px > hist.ma50 * 1.05)):
            return "dca_only"
        if is_buyable_now:
            return "extra_add"
        if px < zones.recommended_low:
            return "deep_add"
        return "wait_pullback"
    if stock_type in {"revaluation_trend", "high_vol_watch"} and chg_pct > 8:
        return "avoid_chase"
    if is_buyable_now:
        return "extra_add"
    if px < zones.recommended_low:
        return "deep_add"
    if px > zones.recommended_high:
        return "wait_pullback"
    return "wait_pullback"


def _current_action(
    *,
    is_buyable_now: bool,
    buyable_tier: str,
    buy_mode: str,
    advice: str,
    p_status: str,
    q_score: int,
    t_score: int,
    chg_pct: float,
    bucket: str,
    gap_score: float,
    stock_type: str,
) -> str:
    if advice == "暂不看" or q_score < 4:
        return "do_not_touch"
    if "重大异动" in advice or abs(chg_pct) > 8:
        return "momentum_alert_only"
    if stock_type in {"revaluation_trend", "high_vol_watch"} and chg_pct > 8:
        return "momentum_alert_only"
    if buy_mode == "dca_only":
        return "dca_only"
    if buyable_tier == "cautious_buyable" and p_status == "合理区间":
        return "cautious_buyable"
    if p_status in {"偏高", "过热"} or buy_mode == "wait_pullback":
        return "wait_pullback"
    if is_buyable_now:
        if t_score < 4 or gap_score < 0:
            return "cautious_buyable"
        if buyable_tier == "priority_buyable":
            return "priority_buyable"
        return "buyable"
    if p_status == "合理区间":
        return "cautious_buyable"
    if p_status == "低于推荐区间":
        return "observe" if t_score < 4 else "cautious_buyable"
    return "observe"


def _row_is_etf_for_book_align(row: OpportunityRow) -> bool:
    """ETF / 大盘底仓：系统「可分批」语义走定投 + 额外加仓带，不参与 typed 买点对齐。"""
    if _is_broad_etf(row.symbol):
        return True
    return str(row.bucket).strip() == "ETF / 大盘底仓"


def _align_opportunity_row_with_book_system(row: OpportunityRow, *, account_equity: float = 25_000.0) -> None:
    """若页面层给了 priority/buyable，再用书籍合成门收紧（非 ETF）。"""
    if _row_is_etf_for_book_align(row):
        return
    if row.current_action not in {"priority_buyable", "buyable"}:
        return
    try:
        from signals.book_card_context import build_book_card_view

        view = build_book_card_view(row, account_equity=account_equity)
    except Exception:
        return
    if view.system_buyable:
        return
    orig = row.current_action
    block_txt = "；".join(view.system_buy_blockers[:6]) if view.system_buy_blockers else "书籍合成门未通过。"
    if row.t_score < 4 or not view.minervini_template_ok or view.regime_label == "risk_off":
        row.current_action = "observe"
        row.buyable_reason = f"【系统门】仅观察：{block_txt}"
    elif view.in_zone:
        row.current_action = "cautious_buyable"
        row.buyable_reason = f"【系统门】谨慎小仓：{block_txt}"
    else:
        row.current_action = "wait_pullback"
        row.buyable_reason = f"【系统门】等回踩/结构：{block_txt}"
    row.advice = _advice_from_action(row.current_action, row.advice)
    row.decision_queue = _queue_from_action(row.current_action)
    row.is_buyable_now = row.current_action in {"priority_buyable", "buyable"}
    row.buyable_tier = row.buyable_tier if row.is_buyable_now else ""
    if orig in {"priority_buyable", "buyable"} and row.current_action not in {"priority_buyable", "buyable"}:
        row.priority_score = max(0.0, float(row.priority_score) - 1000.0)
        row.buy_mode = "wait_pullback"
    row.reason_short = f"{row.label} · {row.advice}"


def _assert_decision_consistency(row: OpportunityRow) -> None:
    z = row.zones
    assert z.deep_value < z.comfortable_buy < row.price < z.avoid_above, {
        "symbol": row.symbol,
        "price": row.price,
        "deep": z.deep_value,
        "pullback": z.comfortable_buy,
        "avoid": z.avoid_above,
    }
    if row.advice == "小仓观察":
        assert row.decision_queue != "现在可分批", row.symbol
    if row.t_score < 4:
        assert row.current_action not in {"priority_buyable", "buyable"}, row.symbol
    if row.zone_position > 1:
        assert row.current_action not in {"priority_buyable", "buyable", "cautious_buyable"}, row.symbol
    if row.price > row.zones.recommended_high:
        assert row.current_action not in {"priority_buyable", "buyable"}, row.symbol
    if row.symbol in {"QQQ", "QQQM", "XLK"} and row.current_action in {"priority_buyable", "buyable"}:
        raise AssertionError(f"{row.symbol} tech ETF should not be promoted to buyable")


def build_opportunity_rows() -> list[OpportunityRow]:
    source_map = _universe_with_source()
    syms = list(source_map)
    quotes = {q.symbol.upper(): q for q in fetch_quotes(syms)}
    sym_to_bucket, _ = _bucket_map()
    meta_map = _symbol_metadata()
    weights = portfolio_bucket_weights()
    spy_hist = _hist_metrics("SPY") or _fallback_hist(100)
    news_map = _load_recent_news_evidence(syms)
    rows: list[OpportunityRow] = []
    for sym in syms:
        q = quotes.get(sym)
        if not q or float(q.px or 0.0) <= 0:
            continue
        px = float(q.px)
        chg = float(q.chg_pct or 0.0)
        hist = _hist_metrics(sym) or _fallback_hist(px)
        meta = meta_map.get(sym, {})
        bucket = _bucket_for(sym, meta, sym_to_bucket)
        stock_type = _stock_type(sym, bucket, meta)
        zones = _safe_calc_typed_entry_zones(
            current_px=px,
            low_3m=hist.low_3m,
            low_6m=hist.low_6m,
            high_3m=hist.high_3m,
            high_52w=hist.high_52w,
            ma50=hist.ma50,
            ma100=hist.ma100,
            ma200=hist.ma200,
            annualized_volatility=hist.vol_1m,
            stock_type=stock_type,
        )
        exits = _safe_calc_exit_zones(current_px=px, ma50=hist.ma50, ma200=hist.ma200, avoid_above=zones.avoid_above)
        q_score = _quality_score(sym, bucket, hist, meta)
        t_score = _trend_score(px, chg, hist, spy_hist.ret_1m, spy_hist.ret_3m)
        p_status, p_score = _position_status(px, zones)
        news_items = news_map.get(sym, [])
        news_score = _news_score(news_items)
        c_score = _catalyst_score(sym, bucket, hist, meta, news_items)
        label = _classify_label(q_score, t_score, p_status, c_score, sym, meta)
        advice = _advice(q_score, t_score, p_status, sym, stock_type, chg, px, hist)
        gap_score, gap_note = _portfolio_gap_score(bucket, weights)
        is_buyable_now, buyable_tier, buyable_reason = _buyable_now_state(
            sym=sym,
            stock_type=stock_type,
            px=px,
            chg_pct=chg,
            zones=zones,
            q_score=q_score,
            t_score=t_score,
            bucket=bucket,
            gap_score=gap_score,
            news_items=news_items,
        )
        buy_mode = _buy_mode(
            sym=sym,
            stock_type=stock_type,
            px=px,
            chg_pct=chg,
            zones=zones,
            is_buyable_now=is_buyable_now,
            bucket=bucket,
            gap_score=gap_score,
            hist=hist,
        )
        zone_pos = _zone_position(px, zones)
        current_action = _current_action(
            is_buyable_now=is_buyable_now,
            buyable_tier=buyable_tier,
            buy_mode=buy_mode,
            advice=advice,
            p_status=p_status,
            q_score=q_score,
            t_score=t_score,
            chg_pct=chg,
            bucket=bucket,
            gap_score=gap_score,
            stock_type=stock_type,
        )
        advice = _advice_from_action(current_action, advice)
        action_rank = {
            "可重点关注": 5,
            "当前可分批": 4,
            "可分批": 4,
            "谨慎小仓": 2,
            "重大异动，不追": 0,
            "只适合定投": 3,
            "小仓观察": 2,
            "等回踩": 1,
            "偏高不追": 0,
            "暂不看": -1,
        }.get(advice, 0)
        overheat_penalty = 1.0 if p_status == "过热" else 0.5 if p_status == "偏高" else 0.0
        overweight_penalty = 1.0 if bucket == "科技 / AI / 半导体" and weights.get(bucket, 0.0) >= 45 else 0.0
        overheat_penalty += _market_overheat_penalty(bucket)
        priority = round(
            (1000 if current_action in {"priority_buyable", "buyable"} else 0)
            + action_rank * 100
            + gap_score * 20
            + q_score * 5
            + t_score * 3
            + c_score * 4
            + news_score * 2
            - overheat_penalty * 30
            - overweight_penalty * 15,
            2,
        )
        queue = _queue_from_action(current_action)
        name = str(meta.get("name") or sym)
        quality_note = str(meta.get("quality_note") or f"{bucket} 候选，结合质量、趋势、位置和催化观察。")
        risk_note = str(meta.get("risk_note") or "候选不等于买入信号，注意仓位和回撤。")
        trend_note = " 便宜但趋势可能变弱，先查原因。" if zones.trend_warning else ""
        news_note = ""
        if news_items:
            top_news = news_items[0]
            news_note = f" 最近新闻：{top_news.get('one_line_zh') or top_news.get('title')}。"
        reason_short = f"{label} · {advice}"
        reason_detail = f"Q={q_score}/10, T={t_score}/10, P={p_status}, C={c_score}/10，News={news_score:+d}，类型={stock_type}。{quality_note}{trend_note}{news_note}"
        hover = (
            f"为什么靠谱：{quality_note}\n\n"
            f"为什么这个建议：当前处于{p_status}，建议为“{advice}”。推荐区间 ${zones.recommended_low:,.2f}-${zones.recommended_high:,.2f}，"
            f"更舒服回踩 ${zones.comfortable_buy:,.2f}，深度回调 ${zones.deep_value:,.2f}，不追位 ${zones.avoid_above:,.2f}。\n\n"
            f"组合语境：{gap_note}"
            + (f"\n\n新闻证据：{news_note.strip()}" if news_note else "")
        )
        row = OpportunityRow(
                symbol=sym,
                name=name,
                sector=classify_sector(sym),
                bucket=bucket,
                source=source_map.get(sym, "core"),
                price=round(px, 2),
                chg_pct=round(chg, 2),
                ret_1w=round(hist.ret_1w, 2),
                ret_1m=round(hist.ret_1m, 2),
                ret_3m=round(hist.ret_3m, 2),
                q_score=q_score,
                t_score=t_score,
                p_score=p_score,
                p_status=p_status,
                c_score=c_score,
                label=label,
                advice=advice,
                priority_score=priority,
                alert_suggestions=_alert_suggestions(label, advice, zones, hist),
                zones=zones,
                exits=exits,
                reason_short=reason_short,
                reason_detail=reason_detail,
                risk_short=risk_note,
                hover_explanation=hover,
                bucket_gap_note=gap_note,
                news_score=news_score,
                news_evidence=news_items,
                decision_queue=queue,
                is_buyable_now=is_buyable_now,
                buyable_tier=buyable_tier,
                buyable_reason=buyable_reason,
                buy_mode=buy_mode,
                current_action=current_action,
                zone_position=round(zone_pos, 3),
                ma50=round(hist.ma50, 2),
                ma200=round(hist.ma200, 2),
                high_52w=round(hist.high_52w, 2),
            )
        _align_opportunity_row_with_book_system(row, account_equity=25_000.0)
        _assert_decision_consistency(row)
        rows.append(row)
    rows.sort(key=lambda r: (r.priority_score, r.q_score, r.t_score), reverse=True)
    return rows


def _template_reason(sym: str, stars: int, chg: float) -> str:
    mood = "走势偏稳，适合分批关注。" if chg >= -2.0 else "近期有回撤，适合等确认再上。"
    return f"{sym} 当前综合评分 {stars}/5。{mood}"


def build_stable_picks() -> list[PickRow]:
    rows: list[PickRow] = []
    for o in build_opportunity_rows():
        stars = max(1, min(5, round((o.q_score + o.t_score + o.p_score) / 6)))
        if stars < 3:
            continue
        blurb = load_weekly_blurb(o.symbol)
        if not blurb:
            blurb = _template_reason(o.symbol, stars, o.chg_pct)
            save_weekly_blurb(o.symbol, blurb)
        rows.append(
            PickRow(
                symbol=o.symbol,
                sector=o.bucket,
                stars=stars,
                current_px=o.price,
                chg_pct=o.chg_pct,
                zones=o.zones,
                exits=o.exits,
                reason=blurb,
                risk=o.risk_short,
            )
        )
    rows.sort(key=lambda r: (r.stars, -r.current_px), reverse=True)
    return rows
