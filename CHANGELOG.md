# Changelog

All notable changes to ThesisWatch are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Truthful Core Loop (branch `overnight/truthful-core-loop`, NOT released)

Honesty-and-integration realignment. Nothing here is tagged or released.

### Added
- **Typed API envelope** (`api_models.py`, ADR-001): every `/api/*` response is
  `{ data, state: ok|stale|partial|unavailable, mode: demo|live, observed_at,
  fetched_at, sources[], warnings[] }`, enforced as a FastAPI `response_model`.
- **Explicit Demo/Live mode** (ADR-004) with a persistent in-app indicator.
- **Merged Thesis Detail** view (ADR-005): Why + Evidence + Thesis unified into one
  continuous, responsive screen (desktop split-view, mobile single-flow) — replaces
  the three overlapping sheets/screens.
- API contract tests (`tests/test_api_contract.py`) and a Playwright smoke test
  (`app/e2e/`), including a live-failure test that asserts an honest `unavailable`
  state instead of silent mock.
- Engineering governance: `HEARTBEAT.md`, `docs/NORTH_STAR.md`,
  `docs/CURRENT_STATE.md`, `docs/DECISIONS.md`, `docs/DECISIONS_FOR_WILLOW.md`,
  `docs/audits/*` (five independent audits).

### Changed
- **No silent mock fallback** (ADR-003): on live failure the UI shows `unavailable`
  (with retry); sample data appears only in explicitly-labeled Demo mode.
- Stopped fabricating freshness: timestamps derive from real records or are `null`
  with a warning (never `datetime.now()` as a stand-in).
- Replaced `except Exception: pass` sites with typed handling that records warnings
  and degrades state to `partial`/`unavailable`.

### Removed / hidden (ADR-008)
- Hidden the unfinished **Ask** panel, the hard-coded **What-if** simulator, and the
  stub **Watchlist** until they have real backends.

## [0.3.0] - 2026-07-24

### Added
- **iOS-style web app** (`app/`) — a mobile-first React + Vite frontend built
  around the core loop **Today → Why changed → Evidence → Stock thesis →
  Portfolio what-if**. Conclusion-first cards, bottom sheets for "why", and a
  fixed evidence structure that visually separates fact from model
  interpretation.
- **FastAPI engine bridge** (`api_server.py`) exposing the existing engine to
  the web UI: `/api/today`, `/api/why/{ticker}`, `/api/evidence/{ticker}`,
  `/api/thesis/{ticker}`, `/api/exposures`, `/api/health`. The web UI reads the
  **same** thesis/conviction/exposure engine as the CLI and Streamlit app.
- **One-command launcher** (`run_web.py`) that starts the API and the frontend
  together and opens the browser; `--live` opts into live data fetches.
- README **Web app** section with screenshots (`docs/screenshots/`).

### Changed
- `requirements.txt` now includes `fastapi` and `uvicorn`.
- Fixed the Streamlit quick-start env var in the README
  (`ALPHAWATCH_OFFLINE=1`).

## [0.2.0] - 2026-07-24

### Added
- **Daily conviction snapshots** — per-holding conviction is recorded over time so
  "what changed since last check" and Why-Changed deltas are grounded in history.

### Changed
- Rebranded the app title to **ThesisWatch** (display name; internal module names
  unchanged to avoid breakage).

## [0.1.0] - 2026-07-24
**thesis-change monitor**. It answers "did my thesis change today, why, and do I
need to do anything?" rather than emitting a buy/sell score. Not financial advice.

### Added

- **Structured thesis ledger** (`tasks/thesis.py`) — a thesis is a typed object
  of claims, catalysts, risks, explicit invalidation conditions, and dated,
  sourced `Evidence` (support/counter), not an LLM paragraph.
- **Calibrated confidence** — a level (`unknown` / `low` / `medium` / `high`)
  plus its drivers (coverage, freshness, support-vs-counter balance); incomplete
  or stale theses honestly can't be "high confidence". Never a bare number.
- **Thesis Delta** (`tasks/thesis_delta.py`) — diffs two thesis snapshots into
  "what changed since last time": confidence before→after, new/removed evidence,
  added/removed risks and claims, direction (strengthened / weakened /
  no material change), and triggered invalidation conditions.
- **Three-state status engine with honest No-action** (`tasks/thesis_status.py`)
  — classifies each holding as 🟢 No action / 🟡 Watch / 🟠 Re-evaluate, and rolls
  holdings into a portfolio summary that plainly says "no action needed today"
  when only price noise moved.
- **Auto-evidence** (`tasks/thesis_autoevidence.py`) — turns today's computed
  signals (conviction factors, guru overlap, news attention, concentration
  flags) into dated `auto:*` evidence and derives which invalidation conditions
  literally fired, so confidence/Delta reflect today's facts without manual entry.
- **Why-changed factor attribution + Evidence sheet** (`tasks/thesis_explain.py`)
  — `factor_delta()` produces the "71 → 78 because +4 Azure, -2 capex" breakdown;
  `classify_evidence()` splits evidence into Fact / Model interpretation /
  Assumption / Counterargument, with freshness and support-vs-counter conflict
  detection.
- **Multi-factor conviction & focus ranking** (`tasks/conviction.py`) — fuses
  rule-based action, RSI, 52-week position, momentum, smart-money overlap, news
  attention, and volatility into a 0–100 conviction with contributing factors;
  `build_focus()` surfaces the top names worth watching. Degrades gracefully when
  optional inputs are absent.
- **Hidden-exposure analysis + what-if simulator** (`tasks/exposure.py`) — maps
  holdings to shared theme factors via a transparent, editable membership map,
  aggregates weighted exposure, and simulates hypothetical trades (recomputing
  exposures, concentration, and cash) without placing any orders.
- **Decision history** (`tasks/decision_history.py`) — an append-only, gitignored
  log that records material status/confidence changes (with reasons and price)
  so calls are accountable and reviewable against later outcomes.
- **Investment Committee** (`tasks/committee.py`) — value / momentum / risk /
  smart-money personas each give a fact-bound view and a Chair synthesizes a
  final call; a risk-manager veto and a numeric fact-guard keep an optional LLM a
  renderer of pre-computed facts, never the source.
- **Daily scan orchestration** (`tasks/thesis_scan.py`) — for each holding:
  auto-derive evidence → merge onto the stored thesis → diff vs the previous
  snapshot → classify → roll up into an honest "Today" headline and brief.
- **Local-first privacy** — holdings (`config/watchlist.yaml`), theses
  (`data/thesis_ledger.json`), decision history, caches, and `.env` are all
  gitignored; only `*.example.*` sample data ships in the repo.
  See `SECURITY.md`.
- **Offline demo** — `ThesisWatch_OFFLINE=1 python -m streamlit run ui/app.py`
  runs against a sample portfolio with no API keys and no network.
- **Streamlit app** (`ui/`) — Today/agent home plus advanced tabs for thesis,
  portfolio risk, holdings editor, committee, dashboard, news, and stock picker.
- **Offline test suite** — pure business logic under `tasks/` covered by
  `python -m pytest -q`; no network, key, or live market required.

[Unreleased]: https://keepachangelog.com/en/1.1.0/
[0.1.0]: https://keepachangelog.com/en/1.1.0/

