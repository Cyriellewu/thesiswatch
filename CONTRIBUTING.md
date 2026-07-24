# Contributing

Thanks for your interest! This project is an **evidence-backed personal thesis
monitor** — it helps an investor track *whether their thesis still holds*, not a
buy/sell signal generator. Contributions should keep it honest, local-first, and
testable.

## Principles (please read before a PR)

1. **Never fabricate.** Facts come from data; the LLM is an optional *renderer*
   of pre-computed facts, never the source. Anything shown as a number must be
   traceable to a signal or user input.
2. **Local-first / privacy.** No user holdings, theses, or keys may enter git,
   logs, or outbound requests. Only `*.example.*` files ship sample data.
3. **Offline-testable core.** Business logic lives in pure functions in `tasks/`
   with unit tests that need no network, no key, and no live market.
4. **Honest degradation.** Missing data becomes a "数据暂缺" state, not a guess.

## Dev setup

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt
ALPHAWATCH_OFFLINE=1 python -m streamlit run ui/app.py
```

## Running tests

```bash
python -m pytest -q            # all offline; no key/network needed
```

Every new rule or engine change needs a unit test. Prefer pure functions in
`tasks/` so they can be tested without Streamlit.

## Good first areas

- New data-provider adapters (behind the existing interfaces)
- Thesis/exposure rule refinements (with a test + rationale)
- UI states: loading / stale / partial / conflicting-evidence
- Docs and examples

## PR checklist

- [ ] No private data or secrets added (only `*.example.*` sample data)
- [ ] New logic has offline unit tests and `pytest -q` passes
- [ ] Change is scoped and explained (what silent failure / user problem it fixes)
