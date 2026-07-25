# Testing + CI + OSS Maintenance Audit (READ-ONLY)
Model: GPT-5.3-Codex · 2026-07-25 · Repo: alphawatch-clean

## 1. Test coverage map
**Covered (Python engine units):** conviction, committee, conviction_history, decision_history, thesis, thesis_delta, thesis_autoevidence, thesis_explain, thesis_scan, thesis_status, exposure; smoke checks for jobs/scheduler, monitors/holdings_monitor.
**NOT covered:**
- FastAPI contract/endpoints — no TestClient, no `/api/*` tests (`api_server.py:94-257`).
- React app — no `*.test.*`/`*.spec.*`, no vitest/jest/playwright.
- Frontend build/typecheck in CI — absent (`.github/workflows/ci.yml:10-39`).
- Demo/live/stale/failed data-state behavior — untested E2E.
- Frontend↔backend schema compatibility — manual, unchecked.
- Core E2E flow Today→Why→Evidence→Thesis — untested.

## 2. CI gap analysis
Today "CI green" = only offline pytest on 3.10/3.11/3.12 (`ci.yml:16,38-39`). Does NOT prove the web app compiles, typechecks, or works against the API.
**Add jobs:** python-unit (keep); api-contract (TestClient for all `/api/*`); frontend-build (Node 20, `npm ci`, `npm run build` = `tsc -b`); playwright-smoke (boot API+app, Today loads + open detail); state-failure-smoke (forced API failure → UI surfaces non-OK, not silent mock success).

## 3. Technical debt / hot spots
- Silent exception swallowing: `api_server.py:71-72,167-168`; `tasks/thesis_scan.py:76-77,85-86` (`except Exception: pass`).
- Mock fallback masks failures: `app/src/data/api.ts:17-24`.
- Hand-maintained dual schema: TS `app/src/types.ts:1-333` vs Python hand-mapped `api_server.py:59-91,109-117,178-182,245-256`; no shared generator/validation.
- State-model mismatch: TS `DataState` has loading/offline/error (`types.ts:15-21`) but API mostly emits "ok"/"partial_data"; stale/error only in mock (`mock.ts:38`).

## 4. OSS-signal honesty audit
Facts: remote github.com/Cyriellewu/thesiswatch; tags v0.1.0/v0.2.0/v0.3.0; **0 PRs**; 5 open roadmap/help-wanted issues; releases for v0.1/0.2/0.3.
Confirmed: ✅ CHANGELOG jumps 0.1→0.3 (no 0.2 section, `CHANGELOG.md:10,31`). ✅ React package `alphawatch-thesis-app` v0.1.0 (`app/package.json:2,4`). ✅ FastAPI version 0.1.0 (`api_server.py:28`). ✅ Issue #4 (Why-Changed sheet) open but implemented (`WhyChangedSheet.tsx`, wired `App.tsx:68-75`). ✅ No HEARTBEAT.md/AGENTS.md on main. ✅ Short/bursty history.
**Honest fixes:** close/retitle #4 with linked commit; add real v0.2.0 CHANGELOG entry + compare links; align versions across API/app/releases; real maintainer signals (review checklist, triage cadence, release notes grounded in shipped tests). Do NOT fabricate usage/adoption/contributors.
### 4b. Retract anti-pattern
Retract "deliberately leave 5 open issues to look maintained." Open issues must reflect real unresolved work only.

## 5. Tonight's minimal must-pass test plan
1. API contract test (envelope): `GET /api/today` asserts keys/types.
2. State test: force degraded/failure → assert surfaced dataState (not silent mock success).
3. Playwright smoke: load Today, open a holding's Why, open Evidence/Thesis, assert headings visible.
**Draft-PR gate:** existing pytest green + new contract tests green + `npm run build` green + Playwright smoke green.
