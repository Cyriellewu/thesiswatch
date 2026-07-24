# ThesisWatch — a personal investment *thesis change* monitor

[![CI](https://github.com/Cyriellewu/thesiswatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Cyriellewu/thesiswatch/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Cyriellewu/thesiswatch)](https://github.com/Cyriellewu/thesiswatch/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

> **Not another buy/sell scorer.** ThesisWatch answers the question you actually
> ask as a long-term holder: *did my investment thesis change today, why, and do
> I need to do anything about it?*

Every day it tells you one of three things per holding — and honestly says
**No action** when nothing at the thesis level changed:

| State | Meaning |
| --- | --- |
| 🟢 **No action** | Just price noise; your core logic is intact. |
| 🟡 **Watch** | New evidence arrived, but no re-judgement yet. |
| 🟠 **Re-evaluate** | A core assumption, risk, or valuation changed — re-check the thesis. |

Local-first, **$0/month**, offline-capable. Your holdings, theses, and keys
never leave your machine.

> ⚠️ **This is not financial advice.** ThesisWatch is a personal research and
> record-keeping tool. It does not place trades and its signals may be wrong or
> based on delayed/incorrect data. Always do your own research and make your own
> decisions. Nothing here is a recommendation to buy or sell any security.

---

## What makes it different

Most tools show you `MSFT 78`. ThesisWatch shows you **what changed and why**:

```
MSFT   71 -> 78   Thesis strengthened
  + Azure outlook        +4
  + Valuation in range   +3
  - AI capex uncertainty -2
  Confidence: medium-high | Evidence coverage: 82% | Freshest evidence: today
```

Core ideas:

- **Structured thesis ledger** — claims, catalysts, risks, explicit
  *invalidation conditions*, and dated evidence (not an LLM paragraph).
- **Calibrated confidence** — a level plus its drivers (coverage, freshness,
  support-vs-counter balance), never a bare number. Stale/incomplete theses
  honestly can't be "high confidence".
- **Thesis Delta** — "what changed since last time", with factor attribution.
- **Evidence, categorized** — Fact / Model interpretation / Assumption /
  Counterargument, with freshness and conflict detection.
- **Hidden exposures + what-if** — see the common factors your "diversified"
  portfolio is actually betting on; simulate trades without placing them.
- **Decision history** — every material call is logged and later scored against
  what actually happened, so the tool is *accountable*.

## Quick start (offline demo, no keys)

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
ThesisWatch_OFFLINE=1 python -m streamlit run ui/app.py
```

The app opens at http://localhost:8501 with a **sample portfolio**
(`config/watchlist.example.yaml`) — no real data, no API keys required.

## Your own portfolio (stays private)

Edit your holdings in the **编辑持仓** tab, or edit `config/watchlist.yaml`
directly (it's created from the example on first run and is **gitignored**, so
your real holdings never enter git). Then fill in your thesis per stock in the
**投资论点** tab.

Optional live data & LLM polish need API keys in `.env` (all free tiers):
yfinance/FRED/FinnHub for data, Gemini -> Groq -> Ollama for optional narration.
See `.env.example`.

## Privacy

Everything personal is local and gitignored: `config/watchlist.yaml`,
`data/thesis_ledger.json`, `data/decision_history.json`, `data/cache/`, `.env`.
Only `*.example.*` files (sample data) are in the repo. See [SECURITY.md](SECURITY.md).

## Development

```bash
python -m pytest -q     # ~100 offline tests; no key/network needed
```

Business logic lives in pure, testable functions under `tasks/`. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).



