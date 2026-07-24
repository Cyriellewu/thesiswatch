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
