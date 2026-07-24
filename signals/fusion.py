from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class SignalBundle:
    symbol: str
    signal_types: list[str]
    bull_score: int
    bear_score: int
    diversity_bonus: int
    recency_bonus: int
    final_score: int
    narrative: str


_BULL_HINTS = {
    "price_breakout",
    "volume_spike",
    "earnings_beat",
    "analyst_upgrade",
    "insider_buy",
}
_BEAR_HINTS = {"macro_strip"}


def _direction_of(signal_type: str, payload: dict[str, Any]) -> str:
    s = str(signal_type or "")
    if s in _BULL_HINTS:
        return "bull"
    if s in _BEAR_HINTS:
        lvl = str(payload.get("_level") or payload.get("implied_alert_level") or "")
        return "bear" if lvl in {"major", "urgent"} else "neutral"
    chg = float(payload.get("chg_pct") or 0.0) if isinstance(payload.get("chg_pct"), (int, float)) else 0.0
    if chg >= 0:
        return "bull"
    return "bear"


def fuse_recent_signals(conn: sqlite3.Connection, *, lookback_hours: int = 168, top_n: int = 20) -> list[SignalBundle]:
    rows = conn.execute(
        """
        SELECT id, symbol, signal_type, payload_json, score, created_at
        FROM market_signals
        WHERE symbol IS NOT NULL
          AND datetime(created_at) > datetime('now', ?)
        ORDER BY datetime(created_at) DESC
        """,
        (f"-{int(lookback_hours)} hours",),
    ).fetchall()
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        sym = str(r["symbol"] or "").upper().strip()
        if not sym:
            continue
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        by_sym.setdefault(sym, []).append(
            {
                "type": str(r["signal_type"] or ""),
                "score": int(r["score"] or 0),
                "created_at": str(r["created_at"] or ""),
                "payload": payload,
            }
        )

    bundles: list[SignalBundle] = []
    now = datetime.now(timezone.utc)
    for sym, sigs in by_sym.items():
        if len(sigs) < 2:
            continue
        bull = 0
        bear = 0
        kinds: set[str] = set()
        newest_age_hours = 9999.0
        for s in sigs:
            kinds.add(s["type"])
            direction = _direction_of(s["type"], s["payload"])
            if direction == "bull":
                bull += int(s["score"])
            elif direction == "bear":
                bear += int(s["score"])
            ts = s.get("created_at")
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                age = (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
                newest_age_hours = min(newest_age_hours, age)
            except Exception:
                pass
        diversity_bonus = len(kinds) * 2
        recency_bonus = 5 if newest_age_hours <= 24 else 0
        final_score = int(bull - bear + diversity_bonus + recency_bonus)
        story = f"{sym} 近7天出现 {len(sigs)} 条信号，类型 {len(kinds)} 种，综合分 {final_score}。"
        bundles.append(
            SignalBundle(
                symbol=sym,
                signal_types=sorted(kinds),
                bull_score=bull,
                bear_score=bear,
                diversity_bonus=diversity_bonus,
                recency_bonus=recency_bonus,
                final_score=final_score,
                narrative=story,
            )
        )

    bundles.sort(key=lambda x: x.final_score, reverse=True)
    return bundles[:top_n]


def persist_bundle_signals(conn: sqlite3.Connection, bundles: list[SignalBundle], *, min_score: int = 30) -> list[int]:
    ids: list[int] = []
    for b in bundles:
        if b.final_score < min_score:
            continue
        payload = {
            "title": f"多信号共振：{b.symbol}",
            "blurb": b.narrative,
            "signal_types": b.signal_types,
            "implied_alert_level": "major" if b.final_score >= 45 else "attention",
            "dedupe_hint": f"bundle:{b.symbol}:{datetime.now(timezone.utc).date().isoformat()}",
        }
        cur = conn.execute(
            """INSERT INTO market_signals (symbol, signal_type, payload_json, score, source)
               VALUES (?,?,?,?, 'fusion_v1')""",
            (b.symbol, "signal_bundle", json.dumps(payload, ensure_ascii=False), b.final_score),
        )
        ids.append(int(cur.lastrowid))
    return ids
