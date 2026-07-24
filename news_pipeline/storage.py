"""SQLite storage for real news."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from news_pipeline.fetchers.base import RawNews, ensure_utc

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    published_at TEXT NOT NULL,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    primary_ticker TEXT,
    affected_tickers TEXT,
    image_url TEXT,
    category TEXT,
    severity TEXT,
    one_line_zh TEXT,
    impact_on_holdings TEXT,
    market_stance TEXT,
    what_it_means_zh TEXT,
    instinct_pushed INTEGER DEFAULT 0,
    cluster_id TEXT,
    storyline_id TEXT,
    user_clicked INTEGER DEFAULT 0,
    user_feedback TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_ticker ON news (primary_ticker);
CREATE INDEX IF NOT EXISTS idx_news_severity ON news (severity);
CREATE INDEX IF NOT EXISTS idx_news_cluster ON news (cluster_id);
"""


def init_news_schema(db_path: Path | str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(news)").fetchall()}
        if "image_url" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN image_url TEXT")
        if "market_stance" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN market_stance TEXT")
        if "what_it_means_zh" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN what_it_means_zh TEXT")
        if "instinct_pushed" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN instinct_pushed INTEGER DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def upsert_news(db_path: Path | str, news_list: list[RawNews], cluster_map: dict[str, list[str]] | None = None) -> int:
    if not news_list:
        return 0
    member_to_cluster: dict[str, str] = {}
    for cid, members in (cluster_map or {}).items():
        for mid in members:
            member_to_cluster[mid] = cid
    conn = sqlite3.connect(str(db_path))
    inserted = 0
    try:
        for item in news_list:
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO news (
                       id, url, source, title, summary, published_at,
                       primary_ticker, affected_tickers, image_url, cluster_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id,
                    item.url,
                    item.source,
                    item.title,
                    item.summary,
                    ensure_utc(item.published_at).isoformat(),
                    item.primary_ticker,
                    json.dumps(item.affected_tickers),
                    item.image_url,
                    member_to_cluster.get(item.id),
                ),
            )
            if conn.total_changes > before:
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    log.info("upsert: %s new news inserted", inserted)
    return inserted


def get_unprocessed_news(db_path: Path | str, limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, title, summary, primary_ticker, affected_tickers, source, published_at
               FROM news
               WHERE category IS NULL
               ORDER BY published_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_news_llm_fields(
    db_path: Path | str,
    news_id: str,
    category: str,
    severity: str,
    one_line_zh: str,
    impact_on_holdings: str | None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """UPDATE news
               SET category = ?, severity = ?, one_line_zh = ?, impact_on_holdings = ?
               WHERE id = ?""",
            (category, severity, one_line_zh, impact_on_holdings, news_id),
        )
        conn.commit()
    finally:
        conn.close()
