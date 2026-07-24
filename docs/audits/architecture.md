# Architecture + Data Contract Audit (READ-ONLY)
Model: GPT-5.6 · 2026-07-25

## 1. Claim verification (against code)
- **CONFIRMED** — silent fallback on fetch/HTTP/timeout: `app/src/data/api.ts:17-24`; no mode/state reaches UI. **PARTIAL** — schema mismatch is NOT detected: JSON is only cast (`api.ts:21`), so a shape mismatch won't even trigger fallback (renders wrong/undefined).
- **CONFIRMED** — Today starts from mock and clears loading even after API failure: `TodayScreen.tsx:22-30`; `api.ts:22-23`.
- **CONFIRMED** — fabricated freshness/state: `api_server.py:88-89,115-116,180,213-217,248-255`. Evidence's real `observed_at` exists in `tasks/thesis.py:37-56` but is **overwritten** with now.
- **CONFIRMED** — `interpretation: ""` (`api_server.py:217`); identical d1/w1/m1 drivers (`:249`); empty conflicts (`:255`); swallowed history errors (`:63-72,160-168`).
- **CONFIRMED** — hand-mapped dict API, no FastAPI response models (`api_server.py:59-91,109-117,178-182,211-220,245-256`) vs hand-maintained TS contracts (`types.ts:87-310`). `EvidenceSheet.tsx:4,17` also sources conflicts from mocks.

## 2. Current data flow
engine dict/dataclass (`willow_agent.build_advice`, `thesis_scan`, `InvestmentThesis`) → ad-hoc bridge dicts (`api_server.py`) → unchecked TS casts (`api.ts:17-34`) → mock-initialized screens.
Divergences: engine evidence timestamps overwritten; bridge state names differ from TS `DataState`; conflicts mocked; d1/w1/m1 duplicated.

## 3. Target architecture
- Pydantic generic `ApiEnvelope[T] { data, state: ok|stale|partial|unavailable, mode: demo|live, observed_at, fetched_at, sources[], warnings[] }` as FastAPI `response_model`.
- **Generate TS types from OpenAPI** (kill the hand-maintained dual schema).
- Domain models: Portfolio, Holding, Thesis, Claim, Evidence, EvidenceSource, ThesisSnapshot, ThesisDelta, DecisionRecord.

## 4. Honest data-state contract
- Derive `observed_at`/`fetched_at` from real source records; `state` from source completeness/age/errors; `mode` explicit (demo only when user chose demo).
- Replace `api_server.py:71-72,167-168` (`except Exception: pass`) with typed handling that appends `warnings[]` and sets `partial`/`unavailable`; **never silently substitute mock**.
- Frontend: persistent Demo/Live + state badges; failures render unavailable/retry, not mock.

## 5. Truthful Core Loop migration (minimal)
Implement enveloped `/api/today` first: real timestamps, derived state, explicit demo mode. Frontend reads envelope, renders state honestly, removes silent fallback (mock only behind explicit Demo toggle, labeled).

## 6. CI gaps
Only offline pytest today (`.github/workflows/ci.yml:10-39`). Add: OpenAPI snapshot / generated-type drift check, Pydantic response tests, frontend build/typecheck, mock-vs-live state tests, E2E for unavailable/stale/demo/live labeling.
