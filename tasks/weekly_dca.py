"""Intra-week dip DCA reminders for ETF weekly contributions (notify only, no broker orders)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from data_layer.market_data import fetch_quotes
from push.notify import send_weekly_dca_recommendation
from stock_picker.zone_context import etf_weekly_dca_usd

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
_ROOT = Path(__file__).resolve().parents[1]


def _load_weekly_dca_config() -> dict[str, Any]:
    p = _ROOT / "config" / "settings.yaml"
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    block = raw.get("weekly_dca") if isinstance(raw.get("weekly_dca"), dict) else {}
    symbols = block.get("symbols") or ["QQQ", "VOO"]
    return {
        "enabled": bool(block.get("enabled", True)),
        "symbols": [str(s).upper() for s in symbols if str(s).strip()],
        "dip_pct_threshold": float(block.get("dip_pct_threshold", -0.5)),
        "friday_force_et": str(block.get("friday_force_et") or "15:00").strip(),
    }


def _week_key(now_et: datetime) -> str:
    iso = now_et.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _parse_hh_mm(raw: str) -> tuple[int, int]:
    parts = raw.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _load_state(conn: Any, week_key: str) -> dict[str, dict]:
    row = conn.execute("SELECT v FROM meta_kv WHERE k = ?", (f"weekly_dca:{week_key}",)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(str(row["v"] or "{}"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(conn: Any, week_key: str, state: dict[str, dict]) -> None:
    conn.execute(
        """INSERT INTO meta_kv (k, v, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = datetime('now')""",
        (f"weekly_dca:{week_key}", json.dumps(state, ensure_ascii=False)),
    )


def is_equity_trading_day(d) -> bool:
    try:
        import exchange_calendars as xcals
        import pandas as pd

        cal = xcals.get_calendar("XNYS")
        ts = pd.Timestamp(d.isoformat(), tz="America/New_York")
        return bool(cal.is_session(ts))
    except Exception:
        return d.weekday() < 5


def get_weekly_dca_status(now_et: datetime | None = None) -> dict[str, Any]:
    """Return current-week DCA state for UI."""

    now_et = now_et or datetime.now(NY)
    cfg = _load_weekly_dca_config()
    week_key = _week_key(now_et)
    from db.client import get_conn

    conn = get_conn(read_only=True)
    try:
        state = _load_state(conn, week_key)
    finally:
        conn.close()

    symbols: list[dict[str, Any]] = []
    for sym in cfg["symbols"]:
        amount = etf_weekly_dca_usd(sym)
        sym_state = state.get(sym, {})
        symbols.append(
            {
                "symbol": sym,
                "weekly_usd": amount,
                "notified": bool(sym_state.get("notified")),
                "trigger": sym_state.get("trigger"),
                "notified_at": sym_state.get("notified_at"),
                "chg_pct": sym_state.get("chg_pct"),
                "px": sym_state.get("px"),
            }
        )
    return {
        "week_key": week_key,
        "enabled": cfg["enabled"],
        "symbols": symbols,
        "dip_pct_threshold": cfg["dip_pct_threshold"],
        "friday_force_et": cfg["friday_force_et"],
    }


def run_weekly_dca_check(now_et: datetime | None = None) -> dict[str, Any]:
    """Check dip / Friday fallback and send ntfy recommendations."""

    cfg = _load_weekly_dca_config()
    if not cfg["enabled"]:
        return {"skipped": True, "reason": "weekly_dca.disabled"}

    now_et = now_et or datetime.now(NY)
    if not is_equity_trading_day(now_et.date()):
        return {"skipped": True, "reason": "not_trading_day", "date": now_et.date().isoformat()}

    week_key = _week_key(now_et)
    fh, fm = _parse_hh_mm(cfg["friday_force_et"])
    is_friday = now_et.weekday() == 4
    friday_force_due = is_friday and now_et.time() >= time(fh, fm)

    from db.client import get_conn

    conn = get_conn(read_only=False)
    pushed = 0
    details: list[dict[str, Any]] = []
    try:
        state = _load_state(conn, week_key)
        symbols = [s for s in cfg["symbols"] if etf_weekly_dca_usd(s)]
        if not symbols:
            return {"skipped": True, "reason": "no_symbols_configured"}

        qmap = {q.symbol.upper(): q for q in fetch_quotes(symbols, ttl_seconds=30)}
        dip_threshold = float(cfg["dip_pct_threshold"])

        for sym in symbols:
            amount = etf_weekly_dca_usd(sym)
            if amount is None or amount <= 0:
                continue
            sym_state = state.get(sym, {})
            if sym_state.get("notified"):
                details.append({"symbol": sym, "action": "already_notified", "trigger": sym_state.get("trigger")})
                continue

            q = qmap.get(sym.upper())
            if not q or q.px <= 0:
                details.append({"symbol": sym, "action": "no_quote"})
                continue

            chg = float(q.chg_pct or 0.0)
            trigger: str | None = None
            if chg <= dip_threshold:
                trigger = "dip"
            elif friday_force_due:
                trigger = "friday_fallback"

            if not trigger:
                details.append({"symbol": sym, "action": "wait", "chg_pct": chg})
                continue

            ok = send_weekly_dca_recommendation(
                symbol=sym,
                amount_usd=float(amount),
                trigger=trigger,
                chg_pct=chg,
                px=float(q.px),
            )
            if ok:
                state[sym] = {
                    "notified": True,
                    "trigger": trigger,
                    "notified_at": now_et.isoformat(),
                    "chg_pct": round(chg, 3),
                    "px": round(float(q.px), 4),
                    "amount_usd": float(amount),
                }
                pushed += 1
                details.append({"symbol": sym, "action": "pushed", "trigger": trigger, "chg_pct": chg})
            else:
                details.append({"symbol": sym, "action": "push_failed", "trigger": trigger})

        _save_state(conn, week_key, state)
        conn.commit()
    finally:
        conn.close()

    stats = {"week_key": week_key, "pushed": pushed, "details": details, "friday_force_due": friday_force_due}
    log.info("weekly DCA check: %s", stats)
    return stats
