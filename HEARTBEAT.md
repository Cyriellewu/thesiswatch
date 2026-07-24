# ThesisWatch Engineering Heartbeat

## North Star

Help a long-term investor determine, in under 30 seconds, whether
new evidence materially changed an investment thesis, and inspect why.

## Current operating mode

- Release freeze: ON (declared 2026-07-25)
- Direct pushes to main: FORBIDDEN
- Current approved slice: Truthful Core Loop (v0.3.1)
- Fake or silent fallback data: FORBIDDEN
- New feature work: PAUSED

## Source-of-truth documents

Before every loop, read:

1. docs/NORTH_STAR.md
2. docs/CURRENT_STATE.md
3. docs/ARCHITECTURE.md
4. docs/DECISIONS.md
5. docs/DECISIONS_FOR_WILLOW.md
6. Current branch diff and test status

## Heartbeat loop

For each meaningful work unit:

1. Restate the user outcome.
2. Check whether the task is inside the approved slice.
3. Inspect existing implementation before editing.
4. Write or identify a failing test.
5. Make the smallest coherent change.
6. Run relevant tests and build.
7. Ask an independent model to review.
8. Record the result below.
9. Stop when the acceptance criterion is met.

## Stop conditions

Stop and record a blocker when:

- the change introduces fabricated or silently substituted data;
- two agents are editing the same files;
- architecture documents disagree with implementation;
- no user-visible outcome can be stated;
- tests cannot distinguish real, demo, stale, and failed states;
- a major product or visual decision requires Willow;
- the work would require merging or releasing.

## Heartbeat entries

### 2026-07-25 00:22 — Phase 0 Release Freeze

- Agent/model: Kiera (orchestrator)
- Task ID: p0-freeze
- User outcome: A trustworthy, single-direction product instead of a fragmented "home mix".
- Action: Declared freeze. Paused all Phase A/B/C feature work. Created branch `overnight/truthful-core-loop`. Set up docs/audits.
- Verified facts: tags v0.1.0/v0.2.0/v0.3.0 all exist; React package `alphawatch-thesis-app@0.1.0` (inconsistent); FastAPI version 0.1.0; CI runs offline pytest only (no frontend build / API contract / E2E).
- Next action: Phase 1 parallel audits (product / ux / architecture / data-and-search / testing-oss), no code edits.
- Willow decision required: no (freeze is per Willow's explicit instruction).

### 2026-07-25 00:33 — Phase 1+2 complete (audits + synthesis)

- Agent/model: Kiera + 5 audit agents (gpt-5.6-sol/terra, opus-4.8, gemini-3.1, gpt-5.3-codex)
- Task IDs: p1-audit-*, p2-synth
- Outcome: 5 independent audits agree — no honest end-to-end task exists today; root cause is silent mock fallback + fabricated freshness + hand-mapped dual schema + fragmented UI.
- Deliverables: docs/audits/{product,ux,architecture,data-and-search,testing-and-oss}.md; docs/NORTH_STAR.md, CURRENT_STATE.md, DECISIONS.md (ADR-001..011), DECISIONS_FOR_WILLOW.md (Q1-Q6); ARCHITECTURE.md web-layer target appended.
- Reviewer: cross-audit consistency (5/5 confirm silent fallback + fabricated timestamps).
- Next action: Phase 3 — implement Truthful Core Loop slice on this branch (typed envelope /api/today, honest states, no silent fallback, hide Ask/What-if, tests). Draft PR only.
- Willow decision required: Q1-Q6 in DECISIONS_FOR_WILLOW.md (proceeding on provisional choices; reversible pre-merge).

### 2026-07-25 00:55 — Phase 3 (1/6): typed API envelope + honest /api/today

- Agent/model: Kiera (implementer)
- Task ID: tcl-envelope (SQL todos reset to the 6-item Truthful Core Loop plan; stale ThesisWatch-API todos from a prior context were removed)
- User outcome: the daily answer now carries an explicit, honest state instead of a fabricated "fresh/ok" — the first step to a trustworthy core loop.
- Action (smallest coherent change): added pure `api_envelope.py` (`ApiEnvelope[T]` per ADR-001 + `today_meta`/`current_mode`, pydantic-only, no engine); rewrote `/api/today` to return the envelope. `state` derived from real `data_degraded`/holdings count (ok/partial/unavailable); `fetched_at` from the engine's real `as_of`; `observed_at` is **null + warning** (per-source time not tracked) instead of `now()`; removed fabricated `updatedAgoMinutes:0`; engine failure returns an explicit `unavailable` envelope (no silent swallow / 500).
- Tests: added `tests/test_api_envelope.py` (7 pure, CI-safe) + `tests/test_api_contract.py` (3 TestClient, skips honestly if engine deps absent); added root `conftest.py`. New tests 10/10 pass.
- Verified facts: full suite 96 passed / 1 failed; the single failure (`test_cache.py::...concurrent_set...`, WinError 5 on a concurrent temp-file rename) reproduces in isolation and is a pre-existing Windows/env flake — `git diff` shows this slice touches only `api_server.py` + new files, not the cache module. Dev note: clean `.venv` was engine-bare; installed the already-declared requirements (fastapi/httpx/pydantic/pandas/numpy/...) to run the API + contract tests.
- Reviewer: independent review pending (next loop).
- Next action: independent review of this diff, then `tcl-frontend` (api.ts: remove silent mock fallback; Demo/Live mode; TodayScreen `unavailable` state) — depends on this envelope.
- Willow decision required: no (within approved slice; branch only, no PR opened yet).

### 2026-07-25 01:13 — Phase 3/4: independent review of the envelope slice + fixes

- Agent/model: Kiera + independent code-review agent (envelope-review)
- Task ID: tcl-envelope (review)
- User outcome: the honesty guarantee now holds inside the payload, not just at the envelope level.
- Review findings (all valid, all fixed this loop): (1) HIGH — `_holding` still stamped `priceObservedAt = now()`, relocating the fabricated-freshness bug one level down into each row; (2) MED — `data.date` was UTC-derived and disagreed with the SGT `fetched_at` by a day before 08:00 SGT; (3) MED — per-row `dataState` read `stock["degraded"]` (never set by the engine) while the envelope counted `data_degraded`, so rows could say "partial_data" while the envelope said "ok".
- Fixes: `_holding.priceObservedAt` → null (engine exposes no per-quote observed time); `willow_agent` now sets per-holding `degraded=True` on fallback so per-row state is truthful; `today_meta` cross-checks `data_degraded` against per-row flags (single source of truth); `/api/today date` derived from the engine SGT `as_of`. Added tests for each.
- Verified facts: live offline payload now honestly reports `state="unavailable"` (all 5 holdings on fallback quotes — previously faked "ok"), `date == fetched_at` day, all rows `priceObservedAt=null`. Full suite 98 passed / 1 failed (same pre-existing `test_cache` Windows concurrency flake; unrelated). Commits d7b6171, ef7e321 on branch.
- Reviewer: envelope-review confirmed `api_envelope.py` derivation honest/correct and scope clean (only /api/today + new files; no version/release change).
- Residual (low sev, noted not fixed): pre-existing `except Exception: pass` at api_server.py:78 (prevConviction fallback) — silent-swallow pattern, no warning channel per-row; defer.
- Next action: `tcl-frontend` — api.ts remove silent mock fallback + Demo/Live mode + TodayScreen `unavailable` state (consumes this envelope).
- Willow decision required: no.


