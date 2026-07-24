from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from data_layer.universe import load_watchlist_tickers
from data_layer.market_data import fetch_quotes
from push.notify import send_alert

NY = ZoneInfo("America/New_York")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _cache_path() -> Path:
    p = _repo_root() / "data" / "cache" / "intraday_alert_dedupe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_dedupe() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_dedupe(v: dict) -> None:
    _cache_path().write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


def _cfg(config: dict | None) -> dict:
    c = dict((config or {}).get("intraday_alerts") or {})
    return {
        "enabled": bool(c.get("enabled", True)),
        "pct_change_threshold": float(c.get("pct_change_threshold", 5.0)),
        "volume_days": int(c.get("volume_days", 5)),
        "volume_multiplier": float(c.get("volume_multiplier", 2.0)),
    }


def _volume_ratio_placeholder(_symbol: str, _days: int) -> float:
    # TODO: replace with real intraday/volume datasource if available.
    return 1.0


def run_intraday_alerts_once(config: dict | None = None) -> dict:
    rules = _cfg(config)
    if not rules["enabled"]:
        return {"enabled": False, "scanned": 0, "triggered": 0}

    syms = load_watchlist_tickers()
    quotes = fetch_quotes(syms)
    qmap = {q.symbol.upper(): q for q in quotes}
    today = datetime.now(NY).date().isoformat()
    dedupe = _load_dedupe()
    sent = 0
    hits: list[dict] = []

    for sym in syms:
        u = sym.upper()
        q = qmap.get(u)
        if not q:
            continue
        chg = float(q.chg_pct or 0.0)
        ratio = _volume_ratio_placeholder(u, rules["volume_days"])
        by_move = abs(chg) >= rules["pct_change_threshold"]
        by_vol = ratio >= rules["volume_multiplier"]
        if not (by_move or by_vol):
            continue
        key = f"{today}:{u}:{int(by_move)}:{int(by_vol)}"
        if dedupe.get(u) == key:
            continue
        title = f"日内异动 {u}"
        body = (
            f"{u} 当日变动 {chg:+.2f}%\n"
            f"成交量倍率(占位) {ratio:.2f}x\n"
            f"触发条件: {'涨跌幅' if by_move else ''}{' + ' if by_move and by_vol else ''}{'放量' if by_vol else ''}"
        )
        ok = send_alert("attention", title, body, tags="rotating_light")
        if ok:
            dedupe[u] = key
            sent += 1
            hits.append({"symbol": u, "chg_pct": chg, "volume_ratio": ratio})

    _save_dedupe(dedupe)
    return {"enabled": True, "scanned": len(syms), "triggered": sent, "hits": hits}

