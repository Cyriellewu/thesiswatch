from __future__ import annotations

"""Auto sentinels for starred symbols.

User intent is deliberately simple: star a ticker, and AlphaWatch watches it
without asking for seven separate alert settings.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from data_layer.market_data import fetch_quotes
from monitors.auto_watch_config import auto_watch_enabled, type_enabled
from stock_picker.practical_zones import calc_practical_zones

NY = ZoneInfo("America/New_York")


SENTINEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist_sentinels (
    user_id TEXT NOT NULL DEFAULT 'default',
    ticker TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    alert_buy_zone INTEGER NOT NULL DEFAULT 1,
    alert_50ma INTEGER NOT NULL DEFAULT 1,
    alert_200ma INTEGER NOT NULL DEFAULT 1,
    alert_drop_5pct INTEGER NOT NULL DEFAULT 1,
    alert_drop_8pct INTEGER NOT NULL DEFAULT 1,
    alert_break_200ma INTEGER NOT NULL DEFAULT 1,
    alert_earnings INTEGER NOT NULL DEFAULT 1,
    reference_price REAL,
    buy_zone_low REAL,
    buy_zone_high REAL,
    comfortable_price REAL,
    deep_value_price REAL,
    PRIMARY KEY (user_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_sentinels_ticker ON watchlist_sentinels(ticker);
"""


@dataclass(frozen=True)
class SentinelRow:
    ticker: str
    added_at: str
    alert_buy_zone: bool = True
    alert_50ma: bool = True
    alert_200ma: bool = True
    alert_drop_5pct: bool = True
    alert_drop_8pct: bool = True
    alert_break_200ma: bool = True
    alert_earnings: bool = True
    reference_price: float = 0.0
    buy_zone_low: float = 0.0
    buy_zone_high: float = 0.0
    comfortable_price: float = 0.0
    deep_value_price: float = 0.0


def _today_et() -> str:
    return datetime.now(NY).date().isoformat()


def _now_et_iso() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def init_sentinel_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SENTINEL_SCHEMA)
    cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(watchlist_sentinels)").fetchall()}
    for name in ("reference_price", "buy_zone_low", "buy_zone_high", "comfortable_price", "deep_value_price"):
        if name not in cols:
            conn.execute(f"ALTER TABLE watchlist_sentinels ADD COLUMN {name} REAL")


def add_sentinel(conn: sqlite3.Connection, ticker: str, *, user_id: str = "default") -> bool:
    init_sentinel_schema(conn)
    symbol = ticker.strip().upper()
    if not symbol:
        return False
    zones = calc_practical_zones(symbol)
    cur = conn.execute(
        """INSERT OR IGNORE INTO watchlist_sentinels
           (user_id, ticker, added_at, reference_price, buy_zone_low,
            buy_zone_high, comfortable_price, deep_value_price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            symbol,
            _now_et_iso(),
            zones.current_price,
            zones.buy_now_low,
            zones.buy_now_high,
            zones.comfortable_price,
            zones.deep_value_price,
        ),
    )
    conn.commit()
    return int(cur.rowcount or 0) > 0


def remove_sentinel(conn: sqlite3.Connection, ticker: str, *, user_id: str = "default") -> bool:
    init_sentinel_schema(conn)
    cur = conn.execute(
        "DELETE FROM watchlist_sentinels WHERE user_id = ? AND ticker = ?",
        (user_id, ticker.strip().upper()),
    )
    conn.commit()
    return int(cur.rowcount or 0) > 0


def is_sentinel_starred(conn: sqlite3.Connection, ticker: str, *, user_id: str = "default") -> bool:
    init_sentinel_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM watchlist_sentinels WHERE user_id = ? AND ticker = ? LIMIT 1",
        (user_id, ticker.strip().upper()),
    ).fetchone()
    return row is not None


def list_sentinels(conn: sqlite3.Connection, *, user_id: str = "default") -> list[SentinelRow]:
    init_sentinel_schema(conn)
    rows = conn.execute(
        """SELECT ticker, added_at, alert_buy_zone, alert_50ma, alert_200ma,
                  alert_drop_5pct, alert_drop_8pct, alert_break_200ma, alert_earnings,
                  reference_price, buy_zone_low, buy_zone_high, comfortable_price, deep_value_price
           FROM watchlist_sentinels
           WHERE user_id = ?
           ORDER BY added_at DESC, ticker ASC""",
        (user_id,),
    ).fetchall()
    return [
        SentinelRow(
            ticker=str(r["ticker"]).upper(),
            added_at=str(r["added_at"] or ""),
            alert_buy_zone=bool(r["alert_buy_zone"]),
            alert_50ma=bool(r["alert_50ma"]),
            alert_200ma=bool(r["alert_200ma"]),
            alert_drop_5pct=bool(r["alert_drop_5pct"]),
            alert_drop_8pct=bool(r["alert_drop_8pct"]),
            alert_break_200ma=bool(r["alert_break_200ma"]),
            alert_earnings=bool(r["alert_earnings"]),
            reference_price=float(r["reference_price"] or 0.0),
            buy_zone_low=float(r["buy_zone_low"] or 0.0),
            buy_zone_high=float(r["buy_zone_high"] or 0.0),
            comfortable_price=float(r["comfortable_price"] or 0.0),
            deep_value_price=float(r["deep_value_price"] or 0.0),
        )
        for r in rows
    ]


def _insert_alert(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    rule: str,
    level: str,
    title: str,
    what: str,
    why: str,
    watch: str,
    risk: str,
    sticky: bool = False,
) -> bool:
    dedupe = f"watchlist-sentinel:{rule}:{ticker}" if sticky else f"watchlist-sentinel:{rule}:{ticker}:{_today_et()}"
    cur = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (level, category, symbol, title, what_happened, why_matters,
            relation_to_you, watch_next, risk, dedupe_key, occurred_at, pushed_ntfy)
           VALUES (?, 'watchlist_sentinel', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            level,
            ticker,
            title,
            what,
            why,
            "你点星关注过这只票，所以 AlphaWatch 自动替你盯，不需要手动设提醒。",
            watch,
            risk,
            dedupe,
            _now_et_iso(),
        ),
    )
    return int(cur.rowcount or 0) > 0


def scan_watchlist_sentinels(conn: sqlite3.Connection, *, max_alerts: int = 20) -> int:
    """Scan all starred tickers and write actionable alerts."""

    if not auto_watch_enabled():
        return 0
    init_sentinel_schema(conn)
    sentinels = list_sentinels(conn)
    if not sentinels:
        return 0

    tickers = [s.ticker for s in sentinels]
    qmap = {q.symbol.upper(): q for q in fetch_quotes(tickers, ttl_seconds=60)}
    inserted = 0

    for s in sentinels:
        q = qmap.get(s.ticker)
        if not q or q.px <= 0:
            continue
        price = float(q.px)
        day_change = float(q.chg_pct or 0.0)
        zones = calc_practical_zones(s.ticker, price=price)
        buy_low = float(s.buy_zone_low or zones.buy_now_low)
        buy_high = float(s.buy_zone_high or zones.buy_now_high)
        comfortable = float(s.comfortable_price or zones.comfortable_price)
        deep_value = float(s.deep_value_price or zones.deep_value_price)

        if s.alert_buy_zone and type_enabled("entry_range") and buy_low <= price <= buy_high:
            inserted += int(
                _insert_alert(
                    conn,
                    ticker=s.ticker,
                    rule="buy-zone",
                    level="major",
                    title=f"{s.ticker} 进入现在就能买区间",
                    what=f"{s.ticker} 当前 ${price:,.2f}，在你点星时记录的买入区 ${buy_low:,.2f}-${buy_high:,.2f} 内。",
                    why="这不是完美估值公式，而是懒人可执行区间：看上的稳健票回到合理位置就提醒。",
                    watch=f"可以小额分批；更舒服的位置约 ${comfortable:,.2f}。",
                    risk="提醒不是命令。若市场情绪刹车亮起，优先小额或继续等回踩。",
                    sticky=True,
                )
            )

        if s.alert_50ma and type_enabled("entry_range") and abs(price - comfortable) / max(comfortable, 0.01) <= 0.012:
            inserted += int(
                _insert_alert(
                    conn,
                    ticker=s.ticker,
                    rule="touch-50ma",
                    level="major",
                    title=f"{s.ticker} 触及 50 日均线附近",
                    what=f"{s.ticker} 当前 ${price:,.2f}，舒适分批位约 ${comfortable:,.2f}。",
                    why="50 日线附近通常是稳健趋势股比较舒服的分批位置。",
                    watch="如果公司逻辑没变，可以考虑小额分批；想更稳就等 200 日线。",
                    risk="跌到均线不是保证反弹，财报/新闻变坏时要先看原因。",
                    sticky=True,
                )
            )

        if s.alert_200ma and type_enabled("entry_range") and abs(price - deep_value) / max(deep_value, 0.01) <= 0.015:
            inserted += int(
                _insert_alert(
                    conn,
                    ticker=s.ticker,
                    rule="touch-200ma",
                    level="urgent",
                    title=f"{s.ticker} 触及 200 日均线附近",
                    what=f"{s.ticker} 当前 ${price:,.2f}，深回调位约 ${deep_value:,.2f}。",
                    why="对长期向上的稳健票来说，200 日线附近通常是少见的深回调位置。",
                    watch="先确认没有重大坏消息；如果逻辑仍在，只做分批，不要梭哈。",
                    risk="跌到 200 日线也可能意味着趋势转弱，必须结合新闻和基本面。",
                    sticky=True,
                )
            )

        if s.alert_drop_5pct and type_enabled("major_move") and day_change <= -5.0:
            inserted += int(
                _insert_alert(
                    conn,
                    ticker=s.ticker,
                    rule="drop-5pct",
                    level="major",
                    title=f"{s.ticker} 单日下跌 {day_change:.1f}%",
                    what=f"{s.ticker} 今日跌幅 {day_change:.1f}%，当前 ${price:,.2f}。",
                    why="你关注的稳健票出现明显回调，可能是买点，也可能有新闻原因。",
                    watch="先查新闻；如果只是市场回调且长期逻辑没变，再考虑分批。",
                    risk="不要看到跌幅就盲目抄底，先确认不是财报或监管问题。",
                )
            )

        if s.alert_drop_8pct and type_enabled("major_move") and day_change <= -8.0:
            inserted += int(
                _insert_alert(
                    conn,
                    ticker=s.ticker,
                    rule="drop-8pct",
                    level="urgent",
                    title=f"{s.ticker} 异常下跌 {day_change:.1f}%",
                    what=f"{s.ticker} 今日跌幅 {day_change:.1f}%，已经不是普通波动。",
                    why="这类跌幅必须看原因：财报、监管、评级、还是市场恐慌。",
                    watch="先看新闻原因；没有明确坏消息时才考虑分批。",
                    risk="异常下跌可能是基本面变化，不能只因为便宜就买。",
                )
            )

        if s.alert_break_200ma and type_enabled("holding_drawdown") and price < deep_value * 0.99:
            inserted += int(
                _insert_alert(
                    conn,
                    ticker=s.ticker,
                    rule="break-200ma",
                    level="major",
                    title=f"{s.ticker} 跌破 200 日均线",
                    what=f"{s.ticker} 当前 ${price:,.2f}，低于深回调位 ${deep_value:,.2f}。",
                    why="这可能意味着长期趋势转弱，和普通回踩不同。",
                    watch="检查原关注理由是否还成立；不要自动加仓。",
                    risk="跌破长期均线后可能继续弱，等待重新站回趋势更稳。",
                )
            )

        if inserted >= max_alerts:
            break

    return inserted
