# Product / North Star Audit (READ-ONLY)
Model: GPT-5.6 · 2026-07-25

## 1. Can any user task be completed end-to-end today? — NO
Users can browse Today → Why → Evidence → Thesis, but:
- API failures **silently substitute mocks** (`app/src/data/api.ts:17-34`).
- API and UI report **misleading freshness** (`api_server.py:88-89,110-117,180,213-216`; `TodayScreen.tsx:58-60`).
- There is **no final decision capture** (no recorded No action/Watch/Re-evaluate outcome).
→ No honest end-to-end task exists.

## 2. Scope tags
| Feature | Tag |
| --- | --- |
| Today statuses | KEEP-CORE |
| Why Changed | KEEP-CORE |
| Evidence | KEEP-CORE |
| Stock Thesis | KEEP-CORE |
| Real exposure analysis | KEEP-SECONDARY |
| Decision history | KEEP-SECONDARY |
| Ask (suggestions only, `App.tsx:81-109`) | HIDE-UNTIL-REAL |
| Hard-coded What-if (`PortfolioScreen.tsx:12-16`) | HIDE-UNTIL-REAL |
| Watchlist (Today data merely re-sorted, `WatchlistScreen.tsx:8-24`) | HIDE-UNTIL-REAL |
| Price-centric conviction display, advanced discovery/committee | DROP from primary loop |

## 3. Truthful Core Loop — acceptance criteria
- Explicit Live/Demo/Error state; never silently fall back to mock.
- Genuine source and observation timestamps (or clearly marked unknown).
- One-click continuity from status → reasons → evidence.
- Materiality traceable to specific thesis claims.
- Final No action / Watch / Re-evaluate decision recorded.
- Whole task usable in under 30 seconds.

## 4. Hide tonight
Ask, hard-coded What-if, stub Watchlist — all present "working" affordances backed by nothing real; they erode trust and must be hidden until they have real backends.

## 5. Decisions Willow must make
1. On API failure: **fail closed** (show unavailable) or offer an **explicitly entered demo mode**?
2. What exact rules constitute a "material thesis change"?
3. Is recording the investor's final decision **mandatory in v1**?
