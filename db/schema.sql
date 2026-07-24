PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta_kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_quotes (
    symbol TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    px REAL NOT NULL,
    chg_pct REAL,
    volume REAL,
    source TEXT NOT NULL DEFAULT 'yfinance_stub',
    PRIMARY KEY (symbol, retrieved_at)
);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_hash TEXT UNIQUE NOT NULL,
    symbol TEXT,
    headline TEXT NOT NULL,
    url TEXT,
    published_at TEXT,
    vendor TEXT NOT NULL DEFAULT 'aggregator',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    embedding_id TEXT
);

CREATE TABLE IF NOT EXISTS push_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL, -- urgent|major|attention|routine
    category TEXT NOT NULL DEFAULT 'holding',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    related_symbol TEXT,
    importance_score INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS opportunity_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    signals_json TEXT NOT NULL DEFAULT '{}',
    score INTEGER NOT NULL DEFAULT 0,
    bucket TEXT DEFAULT 'research',
    blurb TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    variant TEXT NOT NULL, -- takeover|fresh
    style TEXT NOT NULL,   -- conservative|balanced|aggressive
    baseline TEXT NOT NULL DEFAULT 'agent',
    start_equity REAL NOT NULL DEFAULT 10000.0,
    last_equity REAL NOT NULL DEFAULT 10000.0,
    currency TEXT NOT NULL DEFAULT 'USD',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS equity_curve_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    equity REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task TEXT NOT NULL,
    routed_from TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_push_events_occurred ON push_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news_items(symbol, published_at DESC);

-- --------- MVP unified layer: signals + actionable alerts ----------
CREATE TABLE IF NOT EXISTS market_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    signal_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    score INTEGER NOT NULL DEFAULT 0,
    source TEXT DEFAULT 'alpha_pulse',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_market_signals_created ON market_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_signals_sym ON market_signals(symbol, signal_type);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'holding',
    symbol TEXT,
    title TEXT NOT NULL,
    what_happened TEXT NOT NULL DEFAULT '',
    why_matters TEXT NOT NULL DEFAULT '',
    relation_to_you TEXT NOT NULL DEFAULT '',
    watch_next TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT '',
    signal_ids TEXT,
    dedupe_key TEXT UNIQUE,
    occurred_at TEXT NOT NULL,
    pushed_ntfy INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alerts_occurred ON alerts(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_level ON alerts(level);

-- 用户点星关注 = 自动启用默认哨兵提醒；不需要逐只手动配置。
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

CREATE TABLE IF NOT EXISTS agent_accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    mode TEXT NOT NULL,
    style TEXT,
    cash_usd REAL NOT NULL DEFAULT 0,
    starting_cash_usd REAL NOT NULL DEFAULT 0,
    allow_trades INTEGER NOT NULL DEFAULT 1,
    rules_json TEXT NOT NULL DEFAULT '{}',
    agent_goal_json TEXT NOT NULL DEFAULT '{"horizon":"2w","objective":"maximize_return","risk_mode":"risk_aware"}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_positions (
    account_id TEXT NOT NULL REFERENCES agent_accounts(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_cost_usd REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS agent_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES agent_accounts(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    px REAL NOT NULL,
    notional_usd REAL NOT NULL,
    reason TEXT,
    risk_note TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES agent_accounts(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    trigger TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    summary TEXT,
    macro_view TEXT,
    decisions_json TEXT,
    next_check_in_hours INTEGER,
    provider TEXT,
    model TEXT,
    attempts INTEGER,
    error TEXT,
    ctx_snapshot TEXT,
    input_digest_json TEXT,
    decision_json TEXT,
    used_llm INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES agent_accounts(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    equity_usd REAL NOT NULL,
    cash_usd REAL NOT NULL,
    positions_mv_usd REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_eq_acct_ts ON agent_equity_snapshots(account_id, ts DESC);

CREATE TABLE IF NOT EXISTS decision_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES agent_decisions(id) ON DELETE CASCADE,
    eval_horizon TEXT NOT NULL, -- 1d | 7d | 30d
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
    px_ref REAL,
    return_pct REAL,
    outcome TEXT, -- win | loss | breakeven
    details_json TEXT,
    UNIQUE(decision_id, eval_horizon)
);
CREATE INDEX IF NOT EXISTS idx_decision_outcomes_eval ON decision_outcomes(evaluated_at DESC);

CREATE TABLE IF NOT EXISTS signal_effectiveness (
    signal_type TEXT PRIMARY KEY,
    total INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    breakeven INTEGER NOT NULL DEFAULT 0,
    avg_return_pct REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Token accounting for agent / features (older DBs get this via db/migrate.py)
CREATE TABLE IF NOT EXISTS token_usage (
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
);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at DESC);

-- Real news center tables (news is independent from alerts/signals)
CREATE TABLE IF NOT EXISTS news (
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
    market_stance TEXT,
    what_it_means_zh TEXT,
    instinct_pushed INTEGER NOT NULL DEFAULT 0,
    cluster_id TEXT,
    storyline_id TEXT,
    user_clicked INTEGER NOT NULL DEFAULT 0,
    user_feedback TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_ticker ON news(primary_ticker);
CREATE INDEX IF NOT EXISTS idx_news_severity ON news(severity);

CREATE TABLE IF NOT EXISTS storylines (
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
);
CREATE INDEX IF NOT EXISTS idx_storylines_date_rank ON storylines(date DESC, rank ASC);

-- --------- Agent Mini Fund / Agent Arena ----------
-- Isolated agent wallets: same small capital + same info, independent long-only
-- paper portfolios under a deterministic risk engine, immutable decision ledger.
CREATE TABLE IF NOT EXISTS agent_fund (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'baseline',
    model_key TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    benchmark TEXT NOT NULL DEFAULT 'QQQ',
    capital_usd REAL NOT NULL DEFAULT 300,
    cash_usd REAL NOT NULL DEFAULT 300,
    status TEXT NOT NULL DEFAULT 'active',
    execution_mode TEXT NOT NULL DEFAULT 'auto',
    tier TEXT NOT NULL DEFAULT 'paper',
    mandate_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_fund_position (
    fund_id TEXT NOT NULL REFERENCES agent_fund(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_cost_usd REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (fund_id, symbol)
);

CREATE TABLE IF NOT EXISTS agent_fund_intent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id TEXT NOT NULL REFERENCES agent_fund(id) ON DELETE CASCADE,
    cycle_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    snapshot_digest TEXT,
    intent_json TEXT NOT NULL,
    risk_status TEXT NOT NULL,
    risk_reasons_json TEXT NOT NULL DEFAULT '[]',
    counter_json TEXT NOT NULL DEFAULT '{}',
    approved_notional REAL NOT NULL DEFAULT 0,
    cost_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fund_intent_fund_ts ON agent_fund_intent(fund_id, ts DESC);

CREATE TABLE IF NOT EXISTS agent_fund_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id TEXT NOT NULL REFERENCES agent_fund(id) ON DELETE CASCADE,
    intent_id INTEGER REFERENCES agent_fund_intent(id) ON DELETE SET NULL,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    symbol TEXT NOT NULL,
    approved_qty REAL NOT NULL,
    approved_notional REAL NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fund_pending_fund ON agent_fund_pending(fund_id, status);

CREATE TABLE IF NOT EXISTS agent_incident (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    fund_id TEXT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warn',
    message TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_incident_ts ON agent_incident(ts DESC);

CREATE TABLE IF NOT EXISTS agent_fund_trade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id TEXT NOT NULL REFERENCES agent_fund(id) ON DELETE CASCADE,
    intent_id INTEGER REFERENCES agent_fund_intent(id) ON DELETE SET NULL,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    px REAL NOT NULL,
    notional_usd REAL NOT NULL,
    realized_pnl_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fund_trade_fund_ts ON agent_fund_trade(fund_id, ts DESC);

CREATE TABLE IF NOT EXISTS agent_fund_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id TEXT NOT NULL REFERENCES agent_fund(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    equity_usd REAL NOT NULL,
    cash_usd REAL NOT NULL,
    positions_mv_usd REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fund_snapshot_fund_ts ON agent_fund_snapshot(fund_id, ts DESC);

-- Shadow Mode: agent recommendations against the user's REAL portfolio.
CREATE TABLE IF NOT EXISTS agent_shadow_reco (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    action TEXT NOT NULL,
    symbol TEXT,
    notional_usd REAL NOT NULL DEFAULT 0,
    locked_px REAL,
    confidence REAL,
    horizon_days INTEGER NOT NULL DEFAULT 7,
    thesis TEXT,
    invalidation TEXT,
    risk_status TEXT NOT NULL DEFAULT 'approved',
    risk_reasons_json TEXT NOT NULL DEFAULT '[]',
    linked_thesis_json TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_shadow_reco_ts ON agent_shadow_reco(ts DESC);

CREATE TABLE IF NOT EXISTS agent_shadow_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reco_id INTEGER NOT NULL REFERENCES agent_shadow_reco(id) ON DELETE CASCADE,
    horizon TEXT NOT NULL,
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
    px_then REAL,
    return_pct REAL,
    hypothetical_pnl_usd REAL,
    UNIQUE(reco_id, horizon)
);
