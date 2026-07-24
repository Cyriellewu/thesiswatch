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
