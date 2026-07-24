from __future__ import annotations

"""Market sentiment / valuation brake.

This module is deliberately a brake light, not a trading signal. It watches
sentiment and valuation extremes so AlphaWatch can say "pause chasing" or
"do not panic sell" without pretending to time tops and bottoms.
"""

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from data_layer.macro_data import snapshot_macro
from db.client import get_conn
from push.notify import send_alert

NY = ZoneInfo("America/New_York")


@dataclass
class BrakeMetric:
    value: float | None
    status: str
    source: str
    stale: bool = False
    one_week_ago: float | None = None
    one_month_ago: float | None = None
    three_months_ago: float | None = None


@dataclass
class MarketBrakeSnapshot:
    computed_at: str
    vix: BrakeMetric
    fear_greed: BrakeMetric
    qqq_pe: BrakeMetric
    status: str
    status_level: str
    conclusion: str
    triggers: list[dict[str, str]]
    disclaimer: str


def _now() -> datetime:
    return datetime.now(NY)


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT v FROM meta_kv WHERE k = ?", (key,)).fetchone()
    return str(row["v"]) if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO meta_kv (k, v, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = datetime('now')""",
        (key, value),
    )


def _load_points(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    raw = _get_meta(conn, f"market_brake:{name}:history")
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def _save_point(conn: sqlite3.Connection, name: str, value: float | None) -> None:
    if value is None:
        return
    rows = _load_points(conn, name)
    now = _now()
    today = now.date().isoformat()
    rows = [r for r in rows if str(r.get("d")) != today]
    rows.append({"d": today, "v": float(value)})
    cutoff = (now.date() - timedelta(days=110)).isoformat()
    rows = [r for r in rows if str(r.get("d")) >= cutoff]
    _set_meta(conn, f"market_brake:{name}:history", json.dumps(rows, ensure_ascii=False))


def _past_value(points: list[dict[str, Any]], days: int) -> float | None:
    target = _now().date() - timedelta(days=days)
    best: tuple[int, float] | None = None
    for row in points:
        try:
            d = datetime.fromisoformat(str(row.get("d"))).date()
            v = float(row.get("v"))
        except Exception:
            continue
        delta = abs((d - target).days)
        if best is None or delta < best[0]:
            best = (delta, v)
    return best[1] if best else None


def _manual_float(name: str) -> float | None:
    for key in (name, f"ALPHA_{name}", f"ALPHAWATCH_{name}"):
        raw = os.environ.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def _fetch_fear_greed() -> tuple[float | None, str, bool]:
    manual = _manual_float("FEAR_GREED_INDEX")
    if manual is not None:
        return manual, "manual_env", False
    try:
        import requests

        url = os.environ.get("ALPHA_FEAR_GREED_URL", "https://production.dataviz.cnn.io/index/fearandgreed/graphdata")
        resp = requests.get(url, timeout=8, headers={"User-Agent": "AlphaWatch/1.0"})
        resp.raise_for_status()
        data = resp.json()
        value: Any = None
        if isinstance(data, dict):
            fg = data.get("fear_and_greed") or data.get("fearAndGreed")
            if isinstance(fg, dict):
                value = fg.get("score") or fg.get("value")
            value = value if value is not None else data.get("score")
        if value is not None:
            return float(value), "cnn", False
    except Exception:
        pass
    return None, "unavailable", True


def _fetch_qqq_pe() -> tuple[float | None, str, bool]:
    manual = _manual_float("QQQ_PE")
    if manual is not None:
        return manual, "manual_env", False
    # Some yfinance builds expose trailingPE for ETFs, many do not. Treat it as
    # best-effort and fall back to cache/manual.
    try:
        import yfinance as yf

        info = yf.Ticker("QQQ").info or {}
        for key in ("trailingPE", "forwardPE"):
            raw = info.get(key)
            if raw:
                return float(raw), f"yfinance:{key}", False
    except Exception:
        pass
    return None, "unavailable", True


def _cached_or_current(conn: sqlite3.Connection, name: str, current: float | None) -> tuple[float | None, bool]:
    if current is not None:
        _set_meta(conn, f"market_brake:{name}:last", str(float(current)))
        return current, False
    raw = _get_meta(conn, f"market_brake:{name}:last")
    if raw is None:
        return None, True
    try:
        return float(raw), True
    except ValueError:
        return None, True


def _fear_status(value: float | None) -> str:
    if value is None:
        return "数据不可用"
    if value <= 24:
        return "极度恐惧"
    if value <= 44:
        return "恐惧"
    if value <= 55:
        return "中性"
    if value <= 74:
        return "贪婪"
    return "极度贪婪"


def _vix_status(value: float | None) -> str:
    if value is None:
        return "数据不可用"
    if value >= 30:
        return "恐慌升温"
    if value <= 14:
        return "低波动/偏贪婪"
    return "正常"


def _qqq_pe_status(value: float | None) -> str:
    if value is None:
        return "数据不可用"
    if value < 28:
        return "正常或偏便宜"
    if value < 35:
        return "中性偏高"
    if value < 38:
        return "偏贵"
    return "高估警戒"


def _metric(value: float | None, status: str, source: str, stale: bool, points: list[dict[str, Any]]) -> BrakeMetric:
    return BrakeMetric(
        value=round(value, 2) if value is not None else None,
        status=status,
        source=source,
        stale=stale,
        one_week_ago=_past_value(points, 7),
        one_month_ago=_past_value(points, 30),
        three_months_ago=_past_value(points, 90),
    )


def build_market_brake_snapshot(conn: sqlite3.Connection | None = None) -> MarketBrakeSnapshot:
    owns = conn is None
    c = conn or get_conn()
    try:
        macro = snapshot_macro()
        manual_vix = _manual_float("VIX")
        vix_value = float(manual_vix if manual_vix is not None else macro.vix)
        vix_source = "manual_env" if manual_vix is not None else "macro_strip"
        fg_raw, fg_source, fg_stale = _fetch_fear_greed()
        qqq_raw, qqq_source, qqq_stale = _fetch_qqq_pe()
        fg_value, fg_from_cache = _cached_or_current(c, "fear_greed", fg_raw)
        qqq_pe, qqq_from_cache = _cached_or_current(c, "qqq_pe", qqq_raw)

        _save_point(c, "vix", vix_value)
        _save_point(c, "fear_greed", fg_value)
        _save_point(c, "qqq_pe", qqq_pe)

        vix_points = _load_points(c, "vix")
        fg_points = _load_points(c, "fear_greed")
        pe_points = _load_points(c, "qqq_pe")
        vix_1m = _past_value(vix_points, 30)
        fg_1m = _past_value(fg_points, 30)

        triggers: list[dict[str, str]] = []
        if vix_value >= 30:
            triggers.append({"level": "attention", "code": "vix_panic", "title": "VIX 恐慌提醒"})
        if vix_value <= 14:
            triggers.append({"level": "attention", "code": "vix_low", "title": "VIX 低波动提醒"})
        if vix_1m is not None and vix_1m >= 30 and vix_value <= 20:
            triggers.append({"level": "attention", "code": "vix_cooling", "title": "恐慌缓和提醒"})
        if fg_value is not None and fg_value <= 25:
            triggers.append({"level": "attention", "code": "fear_extreme", "title": "市场极度恐惧"})
        if fg_value is not None and fg_value >= 75:
            triggers.append({"level": "attention", "code": "greed_extreme", "title": "市场进入极度贪婪"})
        if fg_1m is not None and fg_value is not None and fg_1m <= 25 and fg_value >= 65:
            triggers.append({"level": "attention", "code": "fast_repair", "title": "情绪修复过快"})
        if qqq_pe is not None and qqq_pe >= 38:
            triggers.append({"level": "major", "code": "qqq_pe_high", "title": "科技估值进入警戒区"})
        if fg_value is not None and qqq_pe is not None and fg_value >= 75 and qqq_pe >= 38:
            triggers.append({"level": "major", "code": "tech_chase_brake", "title": "科技追高刹车"})
        if fg_value is not None and qqq_pe is not None and fg_value >= 80 and vix_value <= 14 and qqq_pe >= 38:
            triggers.append({"level": "urgent", "code": "strong_risk_brake", "title": "强风险刹车触发"})

        if fg_value is not None and qqq_pe is not None and fg_value >= 80 and vix_value <= 14 and qqq_pe >= 38:
            status, status_level = "🔴 风险刹车", "urgent"
            conclusion = "市场同时出现极度贪婪、低波动和科技估值偏热。暂停追高科技股，只设回踩提醒。"
        elif (fg_value is not None and fg_value >= 75) or (qqq_pe is not None and qqq_pe >= 38):
            status, status_level = "🟠 谨慎追高", "major"
            conclusion = "市场情绪或科技估值偏热。现有持仓继续观察，新资金少追高，优先等回踩或补非科技板块。"
        elif (fg_value is not None and fg_value <= 25) or vix_value >= 30:
            status, status_level = "🔵 恐慌机会", "attention"
            conclusion = "市场进入恐惧区。不要情绪化割肉，可以检查核心 ETF 和高质量股票是否进入分批区间。"
        elif fg_value is not None and 45 <= fg_value <= 65 and 15 <= vix_value <= 25:
            status, status_level = "🟡 正常观察", "routine"
            conclusion = "情绪和波动率没有明显极端。按计划执行，不需要因为市场温度额外动作。"
        else:
            status, status_level = "🟡 正常观察", "routine"
            conclusion = "没有同时出现多个极端信号。用它当刹车灯，不把它当买卖信号。"

        snap = MarketBrakeSnapshot(
            computed_at=_now().isoformat(timespec="seconds"),
            vix=_metric(vix_value, _vix_status(vix_value), vix_source, False, vix_points),
            fear_greed=_metric(fg_value, _fear_status(fg_value), fg_source, fg_stale or fg_from_cache, fg_points),
            qqq_pe=_metric(qqq_pe, _qqq_pe_status(qqq_pe), qqq_source, qqq_stale or qqq_from_cache, pe_points),
            status=status,
            status_level=status_level,
            conclusion=conclusion,
            triggers=triggers,
            disclaimer="这些指标是情绪和估值温度计，不是自动买卖信号。",
        )
        _set_meta(c, "market_brake:last_snapshot", json.dumps(asdict(snap), ensure_ascii=False))
        c.commit()
        return snap
    finally:
        if owns:
            c.close()


def load_last_market_brake_snapshot(conn: sqlite3.Connection | None = None) -> MarketBrakeSnapshot | None:
    owns = conn is None
    c = conn or get_conn(read_only=True)
    try:
        raw = _get_meta(c, "market_brake:last_snapshot")
        if not raw:
            return None
        data = json.loads(raw)
        return MarketBrakeSnapshot(
            computed_at=str(data["computed_at"]),
            vix=BrakeMetric(**data["vix"]),
            fear_greed=BrakeMetric(**data["fear_greed"]),
            qqq_pe=BrakeMetric(**data["qqq_pe"]),
            status=str(data["status"]),
            status_level=str(data["status_level"]),
            conclusion=str(data["conclusion"]),
            triggers=list(data.get("triggers") or []),
            disclaimer=str(data.get("disclaimer") or ""),
        )
    except Exception:
        return None
    finally:
        if owns:
            c.close()


def maybe_push_market_brake(conn: sqlite3.Connection | None = None) -> int:
    owns = conn is None
    c = conn or get_conn()
    try:
        snap = build_market_brake_snapshot(c)
        pushed = 0
        today = _now().date().isoformat()
        for trig in snap.triggers:
            code = str(trig.get("code") or "")
            if not code:
                continue
            dedupe = f"{today}:{code}:{snap.status}"
            last = _get_meta(c, f"market_brake:pushed:{code}")
            if last == dedupe:
                continue
            title = f"AlphaWatch: {trig.get('title') or snap.status}"
            body = (
                f"VIX={snap.vix.value}, Fear & Greed={snap.fear_greed.value}, QQQ PE={snap.qqq_pe.value}。\n"
                f"{snap.conclusion}\n"
                "建议：不要把它当买卖信号；恐惧时不乱卖，贪婪时不追高。"
            )
            if send_alert(str(trig.get("level") or "attention"), title, body, tags="brake"):
                _set_meta(c, f"market_brake:pushed:{code}", dedupe)
                pushed += 1
        c.commit()
        return pushed
    finally:
        if owns:
            c.close()


def snapshot_as_dict() -> dict[str, Any]:
    return asdict(build_market_brake_snapshot())
