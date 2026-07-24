"""SQLite 轻量迁移：补上 agent / token_usage 等新列与新表（幂等）。"""

from __future__ import annotations

import sqlite3


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {str(r["name"]) for r in cur.fetchall()}


def _add(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _table_cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS decision_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL REFERENCES agent_decisions(id) ON DELETE CASCADE,
            eval_horizon TEXT NOT NULL,
            evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
            px_ref REAL,
            return_pct REAL,
            outcome TEXT,
            details_json TEXT,
            UNIQUE(decision_id, eval_horizon)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_outcomes_eval ON decision_outcomes(evaluated_at DESC)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS signal_effectiveness (
            signal_type TEXT PRIMARY KEY,
            total INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            breakeven INTEGER NOT NULL DEFAULT 0,
            avg_return_pct REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            feature TEXT NOT NULL,
            agent_id TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            success INTEGER NOT NULL DEFAULT 1,
            fallback_used INTEGER NOT NULL DEFAULT 0,
            reason_for_call TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at DESC)")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS watchlist_sentinels (
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
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_sentinels_ticker ON watchlist_sentinels(ticker)")
    if _table_cols(conn, "watchlist_sentinels"):
        _add(conn, "watchlist_sentinels", "reference_price", "REAL")
        _add(conn, "watchlist_sentinels", "buy_zone_low", "REAL")
        _add(conn, "watchlist_sentinels", "buy_zone_high", "REAL")
        _add(conn, "watchlist_sentinels", "comfortable_price", "REAL")
        _add(conn, "watchlist_sentinels", "deep_value_price", "REAL")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS news (
            id TEXT PRIMARY KEY,
            url TEXT UNIQUE,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            published_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            primary_ticker TEXT,
            affected_tickers TEXT,
            image_url TEXT,
            category TEXT,
            severity TEXT,
            one_line_zh TEXT,
            impact_on_holdings TEXT,
            cluster_id TEXT,
            storyline_id TEXT,
            user_clicked INTEGER NOT NULL DEFAULT 0,
            user_feedback TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_ticker ON news(primary_ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_severity ON news(severity)")
    if _table_cols(conn, "news"):
        _add(conn, "news", "image_url", "TEXT")
        _add(conn, "news", "market_stance", "TEXT")
        _add(conn, "news", "what_it_means_zh", "TEXT")
        _add(conn, "news", "instinct_pushed", "INTEGER NOT NULL DEFAULT 0")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS storylines (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            rank INTEGER,
            title TEXT NOT NULL,
            narrative TEXT,
            representative_tickers TEXT,
            tickers_with_changes TEXT,
            related_news_ids TEXT,
            impact_on_user_holdings TEXT,
            early_movers TEXT,
            intensity INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storylines_date_rank ON storylines(date DESC, rank ASC)")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS price_alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            target_price REAL NOT NULL,
            note TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            triggered_at TEXT,
            last_price REAL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_alarms_active ON price_alarms(active, ticker)")

    # Willow agent memory (Phase 5): run history, per-stock calls, alert cooldown, user rules.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS willow_agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL DEFAULT (datetime('now')),
            headline TEXT,
            portfolio_action TEXT,
            risk_level TEXT,
            trigger TEXT,
            advice_json TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_willow_runs_at ON willow_agent_runs(run_at DESC)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS willow_stock_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            run_at TEXT NOT NULL DEFAULT (datetime('now')),
            ticker TEXT NOT NULL,
            action TEXT,
            chg_pct REAL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_willow_calls_ticker ON willow_stock_calls(ticker, run_at DESC)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS willow_alert_state (
            dedupe_key TEXT PRIMARY KEY,
            ticker TEXT,
            last_action TEXT,
            last_sent_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS willow_user_rules (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )

    if _table_cols(conn, "agent_decisions"):
        _add(conn, "agent_decisions", "success", "INTEGER NOT NULL DEFAULT 1")
        _add(conn, "agent_decisions", "summary", "TEXT")
        _add(conn, "agent_decisions", "macro_view", "TEXT")
        _add(conn, "agent_decisions", "decisions_json", "TEXT")
        _add(conn, "agent_decisions", "next_check_in_hours", "INTEGER")
        _add(conn, "agent_decisions", "provider", "TEXT")
        _add(conn, "agent_decisions", "attempts", "INTEGER")
        _add(conn, "agent_decisions", "error", "TEXT")
        _add(conn, "agent_decisions", "ctx_snapshot", "TEXT")
        _add(conn, "agent_decisions", "action", "TEXT")
        _add(conn, "agent_decisions", "symbol", "TEXT")
        _add(conn, "agent_decisions", "symbol_to", "TEXT")
        _add(conn, "agent_decisions", "dollar_amount", "REAL")
        _add(conn, "agent_decisions", "plain_reason", "TEXT")
        _add(conn, "agent_decisions", "plain_risk", "TEXT")
        _add(conn, "agent_decisions", "next_watch", "TEXT")
        _add(conn, "agent_decisions", "confidence", "REAL")
        _add(conn, "agent_decisions", "model", "TEXT")
        _add(conn, "agent_decisions", "source_alert_ids", "TEXT")
        _add(conn, "agent_decisions", "source_signal_ids", "TEXT")
        _add(conn, "agent_decisions", "raw_json", "TEXT")
        _add(conn, "agent_decisions", "input_digest_json", "TEXT")

    if _table_cols(conn, "agent_trades"):
        _add(conn, "agent_trades", "action", "TEXT")
        _add(conn, "agent_trades", "decision_id", "INTEGER")
        _add(conn, "agent_trades", "plain_reason", "TEXT")

    if _table_cols(conn, "agent_accounts"):
        _add(conn, "agent_accounts", "agent_goal_json", "TEXT")

    # Agent Mini Fund / Agent Arena tables (idempotent for older DBs).
    try:
        from agent_funds.store import DDL as _FUND_DDL  # noqa: PLC0415

        conn.executescript(_FUND_DDL)
        # New columns added after initial ship (v0.3).
        if _table_cols(conn, "agent_fund"):
            _add(conn, "agent_fund", "execution_mode", "TEXT NOT NULL DEFAULT 'auto'")
            _add(conn, "agent_fund", "tier", "TEXT NOT NULL DEFAULT 'paper'")
        if _table_cols(conn, "agent_fund_intent"):
            _add(conn, "agent_fund_intent", "counter_json", "TEXT NOT NULL DEFAULT '{}'")
    except Exception:
        pass

    cols_ag = _table_cols(conn, "agent_accounts")
    default_goal = '{"horizon":"2w","objective":"maximize_return","risk_mode":"risk_aware"}'
    if cols_ag and "agent_goal_json" in cols_ag:
        conn.execute(
            """UPDATE agent_accounts SET agent_goal_json = ?
               WHERE agent_goal_json IS NULL OR trim(agent_goal_json) = ''""",
            (default_goal,),
        )
        per_id_goals = {
            "fresh-aggressive": '{"horizon":"2w","objective":"maximize_return","risk_mode":"risk_aware"}',
            "fresh-balanced": '{"horizon":"3m","objective":"preserve_then_grow","risk_mode":"risk_aware"}',
            "fresh-conservative": '{"horizon":"6m","objective":"preserve_then_grow","risk_mode":"capital_first"}',
            "takeover-balanced": '{"horizon":"1m","objective":"maximize_return","risk_mode":"risk_aware"}',
            "buyhold": '{"horizon":"1y","objective":"preserve_then_grow","risk_mode":"capital_first"}',
        }
        for aid, gj in per_id_goals.items():
            conn.execute(
                """UPDATE agent_accounts SET agent_goal_json = ?
                   WHERE id = ? AND agent_goal_json = ?""",
                (gj, aid, default_goal),
            )

        legacy_goal_upgrades: dict[str, tuple[str, str]] = {
            "fresh-balanced": (
                '{"horizon":"3m","objective":"maximize_return","risk_mode":"risk_aware"}',
                '{"horizon":"3m","objective":"preserve_then_grow","risk_mode":"risk_aware"}',
            ),
            "fresh-conservative": (
                '{"horizon":"6m","objective":"maximize_return","risk_mode":"risk_aware"}',
                '{"horizon":"6m","objective":"preserve_then_grow","risk_mode":"capital_first"}',
            ),
            "buyhold": (
                '{"horizon":"1y","objective":"maximize_return","risk_mode":"risk_aware"}',
                '{"horizon":"1y","objective":"preserve_then_grow","risk_mode":"capital_first"}',
            ),
        }
        for aid, (old_js, new_js) in legacy_goal_upgrades.items():
            conn.execute(
                """UPDATE agent_accounts SET agent_goal_json = ?
                   WHERE id = ? AND agent_goal_json = ?""",
                (new_js, aid, old_js),
            )
