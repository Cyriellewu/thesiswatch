# North Star

> **ThesisWatch helps a long-term investor determine, in under 30 seconds, whether new
> evidence materially changed an investment thesis — and inspect exactly why.**

Output per holding is **No action / Watch / Re-evaluate** — never a buy/sell score.

## The one honest task (v1 must complete this end-to-end)
1. See today's material changes (or an honest "no material change").
2. Pick a holding.
3. See **why** its conviction changed (drivers).
4. Inspect the **evidence** — with fact vs model-interpretation clearly separated, and counter-evidence shown.
5. Decide and (optionally) record: No action / Watch / Re-evaluate.

Whole loop usable in <30 seconds, on desktop and mobile.

## Design principles (inherited, now enforced end-to-end)
- **Never fabricate.** Every number/timestamp is traceable to a real signal or user input, or is explicitly marked unknown. The LLM only *renders* pre-computed facts.
- **Honest states.** The user must always be able to tell real vs demo vs stale vs partial vs failed.
- **One continuous reading path.** Conclusion → change → drivers → evidence → invalidation, not sheet-hopping.
- **Pure core, thin I/O shell.** Business logic stays offline-testable in `tasks/`.

## Non-goals (out of scope for this product's UI)
Real-time/intraday alerts, price alarms, stock discovery/picker, paper trading/virtual accounts, weekly DCA reminders, trade-execution/broker, crypto/forex, social feeds. (Code may remain in the engine; it does not belong in the core loop UI.)

## Explicitly hidden until real
**Ask** (currently suggestions only, no backend) and **What-if** (currently hard-coded) are hidden until they have real backends. **Watchlist** (currently just re-sorted Today data) is hidden or folded into Today.
