# Architecture

AlphaWatch is a local-first **thesis-change monitor**. Its job is to answer, per
holding, one honest question a day: *did my investment thesis change, why, and do
I need to act?* — with an explicit **No action** state when only price moved.

This document describes how the pieces fit together. It is written against the
actual modules in the repository; function and module names below are real.

## Design principles

- **Never fabricate.** Every number is traceable to a signal or user input. The
  LLM is an optional *renderer* of pre-computed facts, never their source.
- **Pure core, thin I/O shell.** Business logic lives in pure functions under
  `tasks/` (no network, no key, no live market) so it is unit-tested offline.
  I/O — quotes, news, persistence, Streamlit — lives at the edges.
- **Honest degradation.** Missing data becomes an explicit "data unavailable" /
  `unknown` state, not a guess. Incomplete or stale theses can't be "high
  confidence".
- **Local-first privacy.** Holdings, theses, decision history, caches, and keys
  never enter git (all gitignored). Only `*.example.*` sample data ships.

## Data flow (the "Today" answer)

```
config/watchlist.yaml                       (private holdings; seeded from *.example)
        │  load_watchlist_tickers() / _positions()
        ▼
tasks/willow_agent.build_advice()           per-stock snapshot: price, action,
        │                                    RSI, 52w position, weights,
        │                                    concentration flags, news
        ▼
tasks/conviction.build_focus()              0–100 conviction + contributing
        │  (score_stock)                     factors; top-N focus ranking
        ▼
tasks/thesis_autoevidence                   signals → dated Evidence(auto:*) +
        │  evidence_from_signals()           signals_for_invalidation()
        │  merge onto stored thesis (thesis_store)
        ▼
tasks/thesis (InvestmentThesis)             claims / catalysts / risks /
        │  .confidence()  .invalidation_hits()   invalidation / evidence
        ▼
tasks/thesis_delta.diff_thesis()            before → after: Δconfidence,
        │  (vs previous snapshot)            new/removed evidence & risks,
        │                                    direction, triggered invalidation
        ▼
tasks/thesis_status.classify()              🟢 no_action / 🟡 watch / 🟠 re_evaluate
        │  summarize_portfolio()             per holding + portfolio roll-up
        ▼
"Today" screen (ui/) + brief_text()         honest headline, incl. "No action
                                             needed today"
```

`tasks/thesis_scan.py` orchestrates this end-to-end: `scan_holding()` runs the
chain for one ticker; `scan_portfolio()` runs every holding and rolls up via
`summarize_portfolio()`; `brief_text()` renders the daily push line. When
`persist=True`, it writes today's snapshot back (so tomorrow has a "before") and
logs material changes to decision history.

Supporting views build on the same `build_advice()` output:

- **Why-changed / Evidence sheet** — `tasks/thesis_explain.factor_delta()` and
  `classify_evidence()`.
- **Hidden exposure + what-if** — `tasks/exposure.compute_exposures()` and
  `simulate()`.
- **Investment Committee** — `tasks/committee.build_committee_facts()` /
  `synthesize_rule_based()` / `run_committee_for_stock()`.
- **Decision history** — `tasks/decision_history.record_if_changed()`.

## Pure vs. I/O separation

- **Pure (offline, unit-tested):** `tasks/thesis.py`, `thesis_delta.py`,
  `thesis_status.py`, `thesis_autoevidence.py`, `thesis_explain.py`,
  `conviction.py`, `exposure.py`, `committee.py` (the `build_*`/`synthesize_*`
  layer), and the pure helpers in `decision_history.py` (`should_log`,
  `make_entry`). These take dicts/dataclasses and return dicts/dataclasses.
- **I/O (edges):** `data_layer/` (market data, news, macro, gurus, caching),
  `tasks/thesis_store.py` and `tasks/decision_history.py` persistence, and
  `ui/` (Streamlit). An optional LLM (`llm/`) only rephrases pre-computed facts
  and is guarded by a numeric fact-check in `committee.factguard_ok()`.
- **Offline switch:** `ALPHAWATCH_OFFLINE=1` makes the data-layer adapters (e.g.
  `market_data`, `free_news`, `macro_data`, `guru_holdings`) skip the network and
  serve sample/cached data, so the whole app and the test suite run without keys.

## Where private data lives (all gitignored)

| Path | Contents |
| --- | --- |
| `config/watchlist.yaml` | Your holdings (seeded from `watchlist.example.yaml`) |
| `data/thesis_ledger.json` | Your theses + previous snapshots (via `thesis_store`) |
| `data/decision_history.json` | Append-only decision log |
| `data/cache/`, `data/*.db*` | Runtime caches / equity data |
| `data/.secret.key`, `data/.broker_creds.enc` | Encrypted broker credentials |
| `.env` | API keys |

Only `*.example.*` files (sample, non-real data) belong in the repo. Resolution
of the private-vs-example watchlist is handled by
`data_layer/watchlist_resolver.watchlist_path()`. See `SECURITY.md`.

## Module map

### `tasks/` — the engine (pure core + orchestration)

| Module | Responsibility |
| --- | --- |
| `thesis.py` | `InvestmentThesis` / `Evidence` model; coverage, freshness, calibrated `confidence()`, `invalidation_hits()` |
| `thesis_delta.py` | `diff_thesis()` → `ThesisDelta` ("what changed since last time") |
| `thesis_status.py` | `classify()` into no_action / watch / re_evaluate; `summarize_portfolio()` |
| `thesis_autoevidence.py` | Signals → dated `auto:*` evidence + invalidation signals map |
| `thesis_explain.py` | `factor_delta()` (why-changed) + `classify_evidence()` (evidence sheet) |
| `thesis_scan.py` | End-to-end daily scan: `scan_holding()`, `scan_portfolio()`, `brief_text()` |
| `thesis_store.py` | Private JSON persistence + previous-snapshot keeping |
| `conviction.py` | Multi-factor conviction `score_stock()` + `build_focus()` ranking |
| `exposure.py` | Theme exposure `compute_exposures()` + what-if `simulate()` |
| `committee.py` | Persona debate + Chair synthesis; risk veto and fact-guard |
| `decision_history.py` | Append-only accountable decision log |
| `willow_agent.py` | `build_advice()` — assembles the daily per-stock advice payload |
| `willow_memory.py` | User rules (concentration limits) + day-over-day call diffs |
| *(others)* | Alerts / pushes / DCA helpers: `buy_zone_alerts`, `intraday_alerts`, `price_alarms`, `daily_news_push`, `news_instinct_push`, `weekly_dca`, `virtual_accounts`, `holding_commentary`, `stock_recommendations` |

### `data_layer/` — I/O adapters (network at the edges)

`market_data`, `free_news` / `news_aggregator` / `news_pipeline`, `macro_data`,
`fred_client`, `finnhub_client`, `guru_holdings` / `smart_money` /
`guru_copybook`, `earnings_data`, `portfolio_analytics`, `cache`, `persist`,
`universe` (`load_watchlist_tickers`), `watchlist_resolver`, `alpaca_client`.
All honor `ALPHAWATCH_OFFLINE`.

### `ui/` — Streamlit presentation

`app.py` is the entry point (`python -m streamlit run ui/app.py`). It renders
`tab_agent_home` (the single-page "Today" command center) plus optional advanced
tabs: `tab_thesis`, `tab_portfolio_risk`, `tab_portfolio_editor`, `tab_committee`,
`tab_dashboard`, `tab_holdings`, `tab_news`, `tab_stock_picker`, `tab_auto_watch`,
`tab_agents`, `tab_tokens`, plus widgets (`native_chart`, `radar_widget`). The UI
only presents data computed by `tasks/`; it never invents numbers.

### Tests

`tests/` mirrors the pure core (`test_thesis`, `test_thesis_status`,
`test_thesis_autoevidence`, `test_thesis_explain`, `test_thesis_scan`,
`test_conviction`, `test_exposure`, `test_committee`, `test_decision_history`,
`test_decision_consistency`, `test_cache`, `test_smoke`, ...). Run with
`python -m pytest -q` — all offline, no key or network needed.
