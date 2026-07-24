# Security Policy

## Your data stays local

This project is **local-first**. Your holdings, investment theses, decision
history, and API keys never leave your machine and are never committed to git:

- `config/watchlist.yaml` — your holdings (gitignored; seeded from
  `watchlist.example.yaml`)
- `data/thesis_ledger.json` — your theses (gitignored)
- `data/decision_history.json` — your decision log (gitignored)
- `data/cache/`, `data/*.db` — runtime caches (gitignored)
- `.env` — your API keys (gitignored)

Never commit any of these. Only `*.example.*` files (with sample, non-real data)
belong in the repository.

## Reporting a vulnerability

If you find a security issue (e.g. a path where private data could leak into git,
logs, or an outbound request), please open a **private** report:

1. Do **not** open a public issue for anything that could expose user data.
2. Email the maintainer or use GitHub's private vulnerability reporting.
3. Include steps to reproduce and the impact.

We aim to acknowledge reports within a few days.

## Scope

In scope: private-data leakage, secret handling, unsafe deserialization,
SSRF via user-supplied URLs. Out of scope: the accuracy of any financial signal
(this is not financial advice — see the disclaimer in the README).
