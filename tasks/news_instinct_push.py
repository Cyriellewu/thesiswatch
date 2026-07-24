"""Push one-line financial-instinct alerts for tagged bullish/bearish news."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from db.client import default_db_path, get_conn
from push.notify import send_news_instinct

log = logging.getLogger(__name__)

_STANCE_LABEL = {"bullish": "利好", "bearish": "利空"}
_ALIASES = {
    "META": ["META", "Meta", "Facebook"],
    "GOOGL": ["GOOGL", "GOOG", "Alphabet", "Google"],
    "MSFT": ["MSFT", "Microsoft"],
    "NVDA": ["NVDA", "Nvidia", "NVIDIA"],
    "AVGO": ["AVGO", "Broadcom"],
    "QQQ": ["QQQ"],
    "VOO": ["VOO"],
}


def _direct_holding_match(title: str, summary: str, holdings: set[str]) -> list[str]:
    text = f"{title} {summary}"
    out: list[str] = []
    for sym in sorted(holdings):
        aliases = _ALIASES.get(sym, [sym])
        if any(re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags=re.I) for alias in aliases):
            out.append(sym)
    return out


def _pick_symbol(row: sqlite3.Row, holdings: set[str]) -> str:
    direct = _direct_holding_match(str(row["title"] or ""), str(row["summary"] or ""), holdings)
    if direct:
        return direct[0]
    pt = str(row["primary_ticker"] or "").upper().strip()
    if pt in holdings:
        return pt
    try:
        affected = json.loads(row["affected_tickers"] or "[]")
    except Exception:
        affected = []
    for sym in affected:
        u = str(sym).upper()
        if u in holdings:
            return u
    return pt or "MARKET"


def push_news_instinct_alerts(
    db_path: str | Path | None = None,
    holdings: list[str] | None = None,
    *,
    limit: int = 5,
) -> dict:
    """Push bullish/bearish instinct lines; skip noise/neutral and already-pushed rows."""

    db = Path(db_path) if db_path is not None else default_db_path()
    hold_set = {s.upper() for s in (holdings or []) if s}
    if not hold_set:
        from data_layer.universe import load_watchlist_tickers

        hold_set = {s.upper() for s in load_watchlist_tickers()}

    conn = get_conn(read_only=False)
    pushed = 0
    skipped = 0
    try:
        rows = conn.execute(
            """SELECT id, title, summary, primary_ticker, affected_tickers, severity,
                      one_line_zh, what_it_means_zh, market_stance
               FROM news
               WHERE category IS NOT NULL
                 AND COALESCE(instinct_pushed, 0) = 0
                 AND market_stance IN ('bullish', 'bearish')
                 AND severity IN ('urgent', 'important', 'attention')
               ORDER BY
                 CASE severity WHEN 'urgent' THEN 0 WHEN 'important' THEN 1 ELSE 2 END,
                 published_at DESC
               LIMIT ?""",
            (limit * 3,),
        ).fetchall()

        for row in rows:
            if pushed >= limit:
                break
            sym = _pick_symbol(row, hold_set)
            direct = _direct_holding_match(str(row["title"] or ""), str(row["summary"] or ""), hold_set)
            sev = str(row["severity"] or "")
            stance = str(row["market_stance"] or "")
            if not direct and sev not in {"urgent", "important"}:
                skipped += 1
                continue

            headline = str(row["what_it_means_zh"] or row["one_line_zh"] or row["title"] or "").strip()
            if not headline:
                skipped += 1
                continue

            ok = send_news_instinct(
                symbol=sym,
                stance=stance,
                headline=headline,
                severity=sev,
            )
            if ok:
                conn.execute("UPDATE news SET instinct_pushed = 1 WHERE id = ?", (row["id"],))
                pushed += 1
            else:
                skipped += 1
        conn.commit()
    finally:
        conn.close()

    stats = {"pushed": pushed, "skipped": skipped, "limit": limit}
    log.info("news instinct push: %s", stats)
    return stats
