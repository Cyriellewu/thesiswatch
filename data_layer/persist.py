from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from data_layer.market_data import Quote


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def persist_quotes(conn: sqlite3.Connection, quotes: list[Quote], *, source: str = "yfinance") -> None:
    ts = _utc_iso()
    for q in quotes:
        conn.execute(
            """INSERT INTO market_quotes (symbol, retrieved_at, px, chg_pct, volume, source)
               VALUES (?,?,?,?,?,?)""",
            (q.symbol.upper(), ts, float(q.px), float(q.chg_pct or 0), None, source),
        )


def _news_hash(symbol: str | None, headline: str, url: str | None = None) -> str:
    base = "|".join([headline.strip().lower(), (symbol or "").upper(), url or ""])
    return hashlib.sha256(base.encode()).hexdigest()


def persist_headlines(conn: sqlite3.Connection, headlines: list[dict]) -> int:
    """写入 news_items（去重）。返回本次新插入条数。"""

    n_new = 0
    for h in headlines:
        sym = (h.get("symbol") or "").upper().strip() or None
        title = (h.get("title") or "").strip()
        if not title:
            continue
        dh = _news_hash(sym, title, None)
        tc = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO news_items (dedupe_hash, symbol, headline, url, published_at, vendor)
               VALUES (?,?,?,?,?,?)""",
            (dh, sym, title, None, _utc_iso(), "pulse"),
        )
        if conn.total_changes > tc:
            n_new += 1
    return n_new
