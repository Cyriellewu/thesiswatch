# Architecture Decisions (ADRs)

Provisional decisions from Phase 2 synthesis. Items needing Willow's call are cross-listed in `DECISIONS_FOR_WILLOW.md`.

## ADR-001 — Single typed API envelope
Every `/api/*` response is a Pydantic `ApiEnvelope[T]`:
```
{ data: T, state: "ok"|"stale"|"partial"|"unavailable",
  mode: "demo"|"live", observed_at, fetched_at, sources[], warnings[] }
```
`state` is derived from source completeness/age/errors; `mode` is explicit. FastAPI `response_model` enforces the shape. **Rationale:** kills fabricated freshness and makes honesty structural.

## ADR-002 — Generate TypeScript from OpenAPI
Stop hand-maintaining `types.ts` alongside Python dicts. Generate TS types from the FastAPI OpenAPI schema; CI fails on drift. **Rationale:** one source of truth; schema mismatch becomes a build error, not a silent render bug.

## ADR-003 — No silent mock fallback
`app/src/data/api.ts` must not return mock on error. On failure the UI renders `unavailable` (with retry). Mock data is served **only** when the user explicitly enters Demo mode, and is always labeled. **Rationale:** the #1 honesty fix.

## ADR-004 — Explicit Demo/Live mode
A persistent, visible mode indicator. Demo = clearly-labeled sample data for trying the product with no keys. Live = real sources; failures show honest states. **Rationale:** user must always know what they're looking at.

## ADR-005 — Merge Why + Evidence + Thesis into one detail view
Replace the three overlapping sheets/screens with a single scrollable Thesis Detail: conclusion → what changed → drivers → inline evidence (incl. counter-evidence) → invalidation conditions, using existing `thesisClaimId` links; driver rows expand to evidence in place. **Rationale:** removes ~6-tap/3-teardown fragmentation; delivers the continuous reading path.

## ADR-006 — Responsive, not a phone strip
Desktop (≥1280px): master–detail split (attention list left, thesis detail right), no modals. Tablet (768px): push-nav. Mobile (390px): single continuous flow. Drop the fixed `max-w-[430px]` shell. **Rationale:** it's a research product, not a blown-up phone.

## ADR-007 — Evidence source-priority ladder + fact/model separation
Authority tiers: 1 SEC/IR/earnings, 2 FRED/Federal Register/gov, 3 structured market data, 4 trusted news/RSS, 5 web search (discovery only), 6 LLM (explain verified evidence only). Render [FACT] (tiers 1–3, needs url+quote) vs [MODEL INTERPRETATION] (tier 4, attributed) vs [ASSUMPTION] (user) vs [COUNTERARGUMENT] (stance=counter). **Rationale:** trustworthy, traceable evidence. (Full pipeline is post-v0.3.1; tonight only stops fabricating timestamps.)

## ADR-008 — Hide unfinished affordances
Ask and What-if are hidden until they have real backends; Watchlist hidden/folded into Today. **Rationale:** no fake "working" buttons.

## ADR-009 — CI must prove the product, not just the engine
Add: api-contract tests (TestClient), `npm run build` + typecheck, OpenAPI/type-drift check, a demo/live/failed-state test, and a Playwright smoke of Today→detail. **Rationale:** "CI green" must mean the core loop actually works.

## ADR-010 — OSS honesty
Close implemented issue #4 (link commit); add real v0.2.0 CHANGELOG entry; align versions (package name/version, FastAPI version) with releases. Never fabricate usage/adoption/contributors/issues. **Rationale:** Codex-for-OSS wants real maintenance evidence.

## ADR-011 — Streamlit demoted to internal lab
The Streamlit app is internal diagnostics/advanced lab, not a second user product. **Rationale:** one product surface.
