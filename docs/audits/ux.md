# AlphaWatch / ThesisWatch — UX & IA Audit
**Scope:** Read-only review of `app/src/` (App.tsx, screens/, components/, index.css, types.ts).
**North Star:** Let a long-term investor decide in <30s whether new evidence materially changed a thesis, and inspect *why* — in one continuous conclusion→evidence read.
**Verdict:** Data model (`types.ts`) is excellent and honest; the *presentation layer* fragments a single mental object ("this stock's thesis") across 4 disjoint surfaces and hides its own data-honesty states. Fix the surface topology, not the data.

---

## 1. Current Information Architecture

### Screen / sheet / tab graph
State lives in `App.tsx` as four independent switches: `tab`, `thesisTicker`, `whyTicker`, `evidenceTicker` (plus `askOpen`). They are **not** modeled as one navigation stack, so the same stock is represented by three parallel, mutually-closing surfaces.

```
                         ┌────────── Sidebar (lg) / BottomNav (<lg) ──────────┐
                         │  Today        Portfolio        Watchlist            │
                         └───────┬──────────────┬───────────────┬─────────────┘
                                 │              │               │
                        TodayScreen       PortfolioScreen   WatchlistScreen
                         (FocusCard)      (exposures +       (rows)
                            │  │  │        what-if)             │
      onOpenThesis (body) ──┘  │  └── onEvidence ──┐            └─ onOpenThesis ─┐
      onWhyChanged ────────────┘                   │                             │
                                                    ▼                             ▼
             WhyChangedSheet  ──"View evidence"──► EvidenceSheet         StockThesisScreen
             (modal, closes ◄── (Why is force-closed) (modal)            (replaces main pane)
              itself on open                                                  │ "All sources"
              of Evidence)                                                    └──► EvidenceSheet (stacks on top)
```

### Tap count for the core task
Task = "understand why ONE stock changed, read the evidence, and see the full thesis," starting on Today:

| Step | Action | Surface transition |
|---|---|---|
| 1 | Tap **Why changed** on FocusCard | Today → WhyChangedSheet |
| 2 | Read ledger, tap **View evidence** | Why sheet *destroyed*, EvidenceSheet opens |
| 3 | Read evidence, tap **Done** | EvidenceSheet closes → back to Today |
| 4 | Tap card **body** | Today → StockThesisScreen (full pane) |
| 5 | Tap **All sources** | EvidenceSheet re-opens over Thesis (duplicate of step 2) |
| 6 | **Done** to dismiss | back to Thesis |

**≈6 taps, 3 context teardowns, and Evidence is read twice.** The "conviction ledger" (Why), the "atomic evidence" (Evidence), and the "full thesis + what-changed + invalidation" (Thesis) are three views of the *same* object but never co-visible. This is the fragmentation the owner describes.

### Every fragmentation point
1. **Three IDs for one entity** (`whyTicker`, `evidenceTicker`, `thesisTicker`) — no shared "selected stock."
2. **Why → Evidence is destructive**: `onOpenEvidence` in App.tsx calls `setWhyTicker(null)` first; the user loses the conviction context they were reading.
3. **Evidence duplicated**: reachable from FocusCard "Sources", from Why "View evidence", and from Thesis "All sources" — three doors, same room, no breadcrumb.
4. **Thesis is a pane-replacement, sheets are modals** — inconsistent depth model (one replaces `main`, others float).
5. **FocusCard is a triple-target button** (body / "Why changed" / "Sources") — three verbs on one card; discoverability + accidental-tap risk.
6. **`whatChanged` and `whatWouldChangeMyMind` live ONLY on the Thesis screen**, while "Why changed" (the daily hook) lives ONLY in a sheet — the two halves of the same story never touch.
7. **Watchlist → Thesis but skips Why/Evidence**; Today → Why/Evidence but body → Thesis — inconsistent entry semantics per surface.
8. **Ask** is context-scoped to `surface` only (`today`/`portfolio`), losing the ticker/claim context the `AskContext` type already supports.

---

## 2. The Core Problem — merge into ONE "Thesis Detail"

Why, Evidence and Thesis are **one continuous document**, not three overlapping surfaces. Collapse `WhyChangedSheet` + `EvidenceSheet` + `StockThesisScreen` into a single scrollable **Thesis Detail** view keyed by one `selectedTicker`. Order it conclusion-first (matches the `types.ts` journey and the sheet docstrings that already promise "conclusion first, evidence last"):

```
Thesis Detail (single scroll, progressive disclosure)
┌─────────────────────────────────────────────┐
│ A. CONCLUSION        StockThesis.oneLiner,    │  always visible, sticky
│                      status, conviction n→n,  │
│                      confidence, coverage%    │
│ B. WHAT CHANGED      whatChanged.d1/w1/m1     │  d1 expanded; w1/m1 collapsed
│                      (was WhyChangedSheet     │
│                       ledger, inlined)        │
│ C. DRIVERS           ConvictionDriver rows,   │  each row expands to its
│                      signed points            │   linked evidence (B↔C↔D share
│                                                │   thesisClaimId — already in model)
│ D. EVIDENCE          supporting[] + risks[]   │  grouped by claim; Fact/
│    (incl. counter-)  interleaved, conflicts   │   Interpretation/Assumption/
│                      surfaced (EvidenceConflict)│  Counterargument tags kept
│ E. INVALIDATION      whatWouldChangeMyMind[]  │  never collapsed (falsifiers)
│ F. CONTEXT           catalysts, decisionHistory│  collapsed by default
└─────────────────────────────────────────────┘
```

**Progressive disclosure rules**
- Default render = A + B(d1) + top-3 drivers + risks + E. Everything else one tap away *in the same scroll*, no modal.
- A driver row (C) expands *in place* to the evidence items (D) sharing its `thesisClaimId` — this is the "conclusion → evidence" read the owner wants, with zero surface teardown.
- **Counter-evidence is never a separate trip**: `risks[]`/`counterargument` items render inline in D, adjacent to what they oppose, with the `EvidenceConflict` banner between the two sides (never averaged away — model already forbids this).
- Delete "View evidence →" and "All sources ›" navigations; they become anchor scrolls, not new surfaces.
- The old sheets become **secondary quick-peek only on mobile** (see §3), rendering the *same* component as the detail sections — single source of truth.

---

## 3. Responsive Rules (Tailwind)

Current app is a phone strip (`max-w-6xl`, `md:grid-cols-2`) with a bolted-on `lg` sidebar; the detail flow ignores width entirely. Introduce a real **master–detail** at desktop.

**1440px (`xl` / `2xl`) — split view**
- Sidebar `w-[248px]` (keep) + **attention list** (Today/Watchlist as left rail, `xl:w-[380px] xl:shrink-0 xl:border-r`) + **Thesis Detail** fills the rest (`xl:flex-1 xl:max-w-[720px]`).
- Selecting a card updates the right pane *without navigation*; no modal ever opens on desktop.
- Layout: `grid xl:grid-cols-[248px_380px_minmax(0,1fr)]`. Right pane owns its own scroll (`xl:overflow-y-auto xl:h-screen`).

**768px (`md`) — two-pane collapses to push-navigation**
- Show list full-width; selecting pushes Thesis Detail as a full-pane route (like today's `StockThesisScreen` pane swap, but now carrying the merged content). Bottom nav stays hidden ≥`lg`, visible `<lg`.
- Cards `md:grid-cols-2` (keep) for the list; detail is single-column always.

**390px (`base`) — single continuous flow**
- List → tap → Thesis Detail as a **full-screen route** (not a bottom sheet) so the whole conclusion→evidence document scrolls as one. Bottom sheets reserved for *Ask* only.
- Sticky conclusion header (`sticky top-0 pt-safe`), `pb-safe` on scroll container.
- Breakpoint map: base = flow, `md:` = list grid + push detail, `lg:` = sidebar appears / bottom nav hides, `xl:` = 3-column split, `2xl:` = cap detail width `max-w-[720px]` + center.

---

## 4. Required States (currently missing or dishonest)

The `DataState` union (`loading | ok | partial_data | stale_data | offline | error`) exists on **every** payload type but is **never read** by any component. `api.ts` silently swaps live data for bundled mock on *any* failure/timeout — the user cannot tell live from demo from stale. This is the "dishonest states" the owner flags. Required treatments:

| State | Trigger | How it must look & read |
|---|---|---|
| **Loading (skeleton)** | `dataState==='loading'` / initial fetch | Use existing `.skeleton` (already defined, unused). Card/section shaped placeholders — **not** today's text "Loading live data…" while mock data is *already shown as if real*. Never render mock under a "live" label. |
| **Stale** | `stale_data` / `EvidenceFreshness.stale` | Amber `--watch` chip "Updated Xh ago — may be outdated" on the conclusion header + per-evidence "stale" tag (FocusCard already shows a tiny `stale`; promote it to header level with timestamp). |
| **Partial** | `partial_data` / `coveragePct` low | Inline "Partial data — N of M sources loaded; coverage 42%" banner; degrade confidence label, don't hide sections. |
| **Unavailable / failed** | `error`, `offline`, `EvidenceFreshness.unavailable` | Honest error card with retry: "Couldn't reach the thesis engine." Do **not** silently fall back to mock. If falling back, label it demo (below). `qualityNote` must render for unavailable evidence. |
| **Empty** | no holdings / `noMaterialChange` only | Calm empty state: "No thesis-level changes today — only price moved." (Today already half-does this; Watchlist/Portfolio have none.) |
| **Demo vs Live** | `api.ts` fell back to bundled mock | Persistent global badge (sidebar footer + mobile header): **"Demo data"** vs **"Live"**. Today's fallback is invisible — the single most important honesty fix. |

Add a `dataState` prop threaded from `api.ts` (it should return which path it took) into every screen; render a shared `<DataStateBanner>` primitive.

---

## 5. Design Tokens / Spacing / Typography (index.css)

**Good, keep:** tabular numerals (`.tnum`, used on all financial figures ✅), status color *always* paired with icon+text (`statusMeta`, `StatusPill` ✅), no purple/AI gradients anywhere (✅ — Ask uses `--text` on `--surface`, calm), 16–20px radii, safe-area vars defined.

**Fix:**
1. **Safe-area is defined but under-applied.** `--safe-top` is only used by `main`'s `pt-safe`; the Thesis sticky header (`sticky top-0`) and the `AskSheet`/`Sheet` bottom sheets on notched devices don't consistently honor `pb-safe`/`pt-safe`. The desktop **Sidebar** uses `h-screen` with no safe padding. Apply `pt-safe`/`pb-safe` to every sticky/fixed edge element.
2. **Numeric hierarchy inconsistency.** The 34px conviction in Thesis and the 26–32px H1s aren't on a token scale — hard-coded `text-[26px] lg:text-[32px]` etc. scattered. Promote a type scale (display/title/body/caption) to `@theme` so the "iOS-calm" rhythm is enforced, not per-file.
3. **Tap targets:** most controls set `min-h-[44px]` ✅, but the FocusCard body button and the DriverChip/rows in Why don't guarantee 44px height — verify all interactive rows hit 44px.
4. **Spacing tokens absent.** Vertical rhythm is ad-hoc (`mt-6`, `mt-7`, `mt-5`, `py-1.5`, `py-2`, `py-3` mixed). Define a spacing scale token set; the merged detail view needs one consistent section gap.
5. **`--calm-bg` reused for both `no_action` status AND `fact`/`assumption` type tags** (`TYPE_META`) — a Fact and a "No action" pill read as the same neutral swatch. Give evidence-type tags their own low-chroma palette so provenance (Fact vs Assumption vs Counterargument) is scannable.
6. **Contrast:** `--text-3` (#9ca3af) on `--surface-2` (#f7f7fa) for timestamps/hints is ~2.3:1 — below WCAG AA for small text. Darken tertiary or enlarge.
7. Motion tokens (160–240ms spring) are good; ensure `prefers-reduced-motion` disables `sheet-in`/`skeleton`.

---

## 6. Wireframes (merged Thesis Detail)

### 6a. Desktop split-view @1440px
```
┌──────────┬───────────────────────┬──────────────────────────────────────────┐
│ Sidebar  │  ATTENTION LIST        │  THESIS DETAIL — MSFT            [Live ●]  │
│          │  (Today / Watchlist)   │ ┌────────────────────────────────────────┐│
│ ThesisW. │ ┌────────────────────┐ │ │ MSFT  ◍ Watch    conviction 71 → 78  ▲ ││ sticky
│ 论点监测 │ │▸ MSFT  ◍  71→78  ▲│◄┼─┤ │ "Cloud reacceleration intact; …"       ││ conclusion
│          │ │  Thesis strengthen │ │ │ Confidence: High · Coverage 84%        ││
│ ◎ Today  │ ├────────────────────┤ │ ├────────────────────────────────────────┤│
│ ◧ Portf. │ │  NVDA  ⟳  62→55  ▼│ │ │ WHAT CHANGED   [Today][Week][Month]    ││
│ ☆ Watch  │ │  Re-evaluate       │ │ │  +6 Azure bookings  +3 margin  −1 FX   ││
│          │ ├────────────────────┤ │ ├────────────────────────────────────────┤│
│          │ │  AAPL  ✓  70→70   │ │ │ DRIVERS ▸ tap a row → evidence inline  ││
│          │ │  No material change │ │ │  ▾ +6 Azure bookings   [Fact ↗ Reuters]││
│          │ └────────────────────┘ │ │      └ counter: −? seasonality [Assump]││
│          │  ── No material (5) ▾  │ │ ├────────────────────────────────────────┤│
│          │                        │ │ │ EVIDENCE (incl. counter) ⚠ 3 pos·1 neg ││
│ [Ask     │                        │ │ ├────────────────────────────────────────┤│
│  Alpha]  │                        │ │ │ ✕ WOULD CHANGE MY MIND (invalidation)  ││
│ Demo/Live│                        │ │ │ ▸ Catalysts · Decision history  (▾)    ││
└──────────┴───────────────────────┴─┴────────────────────────────────────────┴┘
   select on left → right pane updates, NO modal, NO teardown
```

### 6b. Mobile single-flow @390px
```
┌───────────────────────────┐   ┌───────────────────────────┐
│ ‹ Today        [Demo data] │   │  … (continued scroll) …    │
│ MSFT      ◍ Watch          │   │ DRIVERS                    │
│ 71 → 78 ▲   Conf High·84%  │◄sticky │ ▾ +6 Azure bookings   │
│ "Cloud reaccel intact…"    │   │    Fact · Reuters ↗ · 2h   │
├───────────────────────────┤   │    interpretation: …       │
│ WHAT CHANGED               │   │   ⚠ conflict 3 pos · 1 neg │
│ [Today] Week  Month        │   │ ▸ +3 margin expansion      │
│  +6 Azure  +3 margin  −1FX │   ├───────────────────────────┤
│ (tap driver ↓ scrolls to   │   │ EVIDENCE incl. counter-    │
│  its evidence, same page)  │   │  [Fact][Counterargument]…  │
├───────────────────────────┤   ├───────────────────────────┤
│ CURRENT THESIS  • • •      │   │ ✕ WOULD CHANGE MY MIND     │
├───────────────────────────┤   │  • Azure growth <15% 2 qtrs│
│ (one continuous scroll —   │   ├───────────────────────────┤
│  no sheets, no re-opening) │   │ ▸ Catalysts ▸ History      │
│            …               │   │ [Ask about MSFT]  pb-safe  │
└───────────────────────────┘   └───────────────────────────┘
   ONE route, ONE scroll: conclusion → change → drivers → evidence → invalidation
```

---

## Priority fixes
1. **Collapse `whyTicker`/`evidenceTicker`/`thesisTicker` → one `selectedTicker`; merge the two sheets into `StockThesisScreen` as inline sections** (§1, §2). Removes ~4 taps and all teardown.
2. **Wire `DataState` + demo/live badge into the UI; stop silently serving mock as live** (§4).
3. **Desktop master–detail at `xl`, full-screen flow at base** (§3).
4. Type/spacing scale tokens + safe-area on all fixed edges + tertiary-text contrast (§5).
