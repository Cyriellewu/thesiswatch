# Decisions for Willow (morning review)

These are product/direction calls the orchestrator intentionally did NOT make alone. Provisional choices are marked; overnight work proceeds on the provisional choice but stays in a draft PR so any of these can be reversed before merge.

## Q1 — On API/data failure: fail closed, or explicit Demo mode?
- **Option A (provisional):** Fail closed — show "unavailable / retry", never show sample data unless the user explicitly switches to Demo mode.
- Option B: Auto-enter a clearly-labeled Demo mode on failure.
- Trade-off: A is maximally honest; B is friendlier for first-run with no keys. Provisional = A + a manual Demo toggle.

## Q2 — What exactly is a "material thesis change"?
Need a concrete rule set so Watch vs Re-evaluate is principled, e.g.:
- Re-evaluate if: a thesis claim's supporting fact is contradicted by a tier 1–3 source; OR an invalidation condition is met; OR conviction crosses a threshold on fundamed drivers (not price).
- Watch if: new tier 1–4 evidence appears but no claim/invalidation is broken.
- **Provisional:** the above; needs your confirmation/edits.

## Q3 — Is recording the user's final decision mandatory in v1?
- Option A (provisional): Optional "I've reviewed → No action/Watch/Re-evaluate" capture, stored in decision history.
- Option B: Mandatory to complete the loop.
- Trade-off: A is lighter; B guarantees the loop closes and builds a real decision log.

## Q4 — Product scope boundary
Keep ThesisWatch a **personal investment thesis monitor**, or gradually generalize into a reusable **Evidence / Thesis-Delta engine**? Provisional: stay personal-investing for v1; keep engine modular so generalization stays possible.

## Q5 — UI structure
Confirm ADR-005/006: merged Thesis Detail + responsive split-view (desktop) / single-flow (mobile), dropping the 430px phone shell. Provisional: yes.

## Q6 — Unfinished features
Confirm hiding Ask and What-if until real (vs labeling them "Preview"). Provisional: hide.

## Q7 — BLOCKER: two parallel envelope implementations collided on this branch (needs your call)
While the overnight loop was mid-slice, a second commit landed on `overnight/truthful-core-loop`:
`39f292a "api: add typed honesty envelopes"` (author Cyriellewu, 2026-07-24 18:34 BST / 2026-07-25 01:34 SGT).
It independently implements the SAME typed-envelope feature the loop was building, so the branch now
has **two implementations of the same idea**:

- **A — `api_models.py` + rewritten `api_server.py`** (the `39f292a` commit): broader — envelopes
  **all** endpoints (today/exposures/why/evidence/thesis), `Envelope[T]` with datetime-typed
  timestamps. Tests: `tests/test_api_contract.py` (rewritten). PASSES.
- **B — `api_envelope.py` + `/api/today` only** (the loop's commits d7b6171/ef7e321): narrower but with
  two honesty properties A currently lacks (see below). Tests: `tests/test_api_envelope.py`. PASSES.
  `api_envelope.py` is now **orphaned** — `api_server.py` at HEAD imports A, not B.

Current state is NOT broken: both test sets pass (12), and the committed frontend consumes
`/api/today`'s envelope and works with A. The problem is duplication + a couple of honesty regressions in A.

**Honesty concerns found in A (`api_server.py` @ 39f292a), which B had deliberately avoided:**
1. A sets `observed_at = as_of` (the engine's **compute** time), conflating it with per-source
   **observation** time. B concluded `as_of` is fetch-time only and set `observed_at = null` + a warning.
2. Timezone bug: A parses the SGT `as_of` string as UTC (`...Z`). Live payload shows
   `observed_at = 2026-07-25T00:38:00Z` occurring AFTER `fetched_at = 2026-07-24T17:38:10Z` — an
   internal contradiction (`updatedAgoMinutes` then floors to 0).
3. A still emits per-holding `priceObservedAt` + `updatedAgoMinutes` (hardcoded in its demo branch);
   the frontend no longer uses these, but they remain in the payload.

**Decision needed:** pick ONE canonical envelope module and delete the other, then reconcile A's
timestamp honesty (null-or-real `observed_at`, fix SGT→UTC). Recommended: **keep A's breadth**
(all endpoints enveloped) but **port B's timestamp honesty into it** (observed_at null-not-`as_of`,
correct SGT offset), then delete `api_envelope.py`/`test_api_envelope.py`. The loop did NOT delete or
merge either side — this is your call and it touches files another actor is editing.

**Process note:** per the freeze's HARD RULES ("two agents must not edit the same files") the loop has
STOPPED touching the backend envelope files (`api_server.py`, `api_models.py`, `api_envelope.py`,
`tests/test_api_contract.py`) to avoid a concurrent-edit collision, and is holding further backend work
until you resolve ownership.

