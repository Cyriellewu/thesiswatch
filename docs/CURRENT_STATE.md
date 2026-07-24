# Current State (factual, 2026-07-25)

Synthesis of 5 independent audits (product/GPT-5.6, ux/Opus-4.8, architecture/GPT-5.6, data-and-search/Gemini-3.1, testing-oss/GPT-5.3-Codex). File+line references verified in code.

## Verdict
**No honest end-to-end task can be completed today.** The engine is real and unit-tested; the web layer that sits on top presents fabricated freshness and silently substitutes mock data on failure, so a user cannot trust or complete the core loop.

## What actually works
- Python engine (`tasks/`): thesis ledger, conviction, exposure, decision history, committee — unit-tested offline.
- The four core screens render and navigate (Today → Why → Evidence → Thesis).
- Real exposure computation is wired (`/api/exposures`).

## What is dishonest or fake (must fix/hide)
| Problem | Evidence (file:line) | Fix |
| --- | --- | --- |
| Silent mock fallback on any fetch error/timeout | `app/src/data/api.ts:17-24` | Remove; show `unavailable` |
| Today inits from mock, ends loading even on failure | `TodayScreen.tsx:22-30` | Honest state machine |
| Schema mismatch not even detected (blind cast) | `api.ts:21` | Validate against generated types |
| Fabricated freshness (observed/fetched=now, updatedAgo=0) | `api_server.py:88-89,115-116,180,213-217` | Derive from real records or mark unknown |
| Evidence real `observed_at` overwritten with now | engine has it at `tasks/thesis.py:37-56`; overwritten in `api_server.py` | Pass through real value |
| `interpretation:""`, identical d1/w1/m1, conflicts always [] | `api_server.py:217,249,255` | Real values or mark absent |
| `except Exception: pass` swallows errors | `api_server.py:71-72,167-168`; `tasks/thesis_scan.py:76-77,85-86` | Typed handling → warnings + partial/unavailable |
| Hand-maintained dual schema (Py dicts vs TS types) | `api_server.py:59-91,...` vs `types.ts:87-310` | Pydantic response_model + generate TS from OpenAPI |
| Fake Ask (suggestions only) | `App.tsx:81-109` | Hide until real |
| Hard-coded What-if | `PortfolioScreen.tsx:12-16` | Hide until wired to `exposure.simulate` |
| Stub Watchlist (re-sorted Today) | `WatchlistScreen.tsx:8-24` | Hide/fold into Today |
| Fixed 430px phone shell; sheet-hopping (~6 taps, 3 teardowns) | App.tsx / screens | Responsive split-view + merged detail |

## OSS hygiene gaps (real, fixable honestly)
- CHANGELOG jumps v0.1→v0.3 (no v0.2 section) though v0.2.0 tag exists.
- React package `alphawatch-thesis-app@0.1.0`; FastAPI `version=0.1.0` — inconsistent with released tags.
- Issue #4 (Why-Changed sheet) implemented but still OPEN.
- 0 PRs; no HEARTBEAT/AGENTS on main (HEARTBEAT now added on this branch).
- **Retracted anti-pattern:** "deliberately leave 5 open issues to look maintained." Open issues must reflect real unresolved work only.

## Testing/CI gaps
Only offline pytest (py3.10-3.12). No api-contract test, no `npm run build`, no typecheck, no E2E, no demo/live/failed-state test in CI.
