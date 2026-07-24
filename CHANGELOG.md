# Changelog

All notable changes to ThesisWatch are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-24

First public release of ThesisWatch — a local-first personal investment
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

