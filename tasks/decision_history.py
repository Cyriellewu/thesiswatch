"""Decision history — an append-only log of thesis status/confidence changes.

Makes the agent's calls *accountable and reviewable*: every time a holding's
status or confidence moves, we record a dated entry (with the reasons and the
price at the time). Later, the entry can be scored against what actually
happened (outcome), so the user can see which signals were right and which the
agent kept getting wrong.

Stored in the private thesis ledger file (gitignored). Pure logic is separated
from I/O so it's testable offline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
_STORE = _REPO / "data" / "decision_history.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- pure logic -----------------------------------------------------------


def should_log(prev_entry: dict[str, Any] | None, status: str, confidence: float) -> bool:
    """Log only material changes: first sighting, a status change, or a
    confidence move >= 4 points. Avoids spamming the timeline daily."""
    if prev_entry is None:
        return True
    if prev_entry.get("status") != status:
        return True
    return abs(float(confidence) - float(prev_entry.get("confidence", confidence))) >= 4.0


def make_entry(
    ticker: str,
    status: str,
    confidence: float,
    reasons: list[str],
    price: float | None = None,
    *,
    at: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "at": at or _now_iso(),
        "status": status,
        "confidence": round(float(confidence), 1),
        "reasons": list(reasons or []),
        "price": price,
        "outcome_price": None,       # filled in later by score_outcome
        "outcome_pct": None,
        "outcome_at": None,
    }


def score_outcome(entry: dict[str, Any], later_price: float, *, at: str | None = None) -> dict[str, Any]:
    """Attach the realized price move since the decision, so the call can be
    judged. Non-mutating: returns an updated copy."""
    out = dict(entry)
    p0 = entry.get("price")
    if p0 and later_price:
        out["outcome_price"] = later_price
        out["outcome_pct"] = round((later_price / p0 - 1.0) * 100.0, 2)
        out["outcome_at"] = at or _now_iso()
    return out


def accuracy_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Simple, honest calibration: for scored re_evaluate/watch calls, did the
    subsequent move agree with the direction implied by the status? This is
    descriptive, not a performance guarantee."""
    scored = [e for e in entries if e.get("outcome_pct") is not None]
    n = len(scored)
    if n == 0:
        return {"scored": 0, "note": "还没有可评估的历史(需要后续价格回填)。"}
    # 'strengthened/re_evaluate up' should precede a rise; weakened a fall.
    agree = 0
    for e in scored:
        pct = e["outcome_pct"]
        reasons = " ".join(e.get("reasons", []))
        bullish = "增强" in reasons or "支持" in reasons
        bearish = "减弱" in reasons or "失效" in reasons or "风险" in reasons
        if bullish and pct > 0:
            agree += 1
        elif bearish and pct < 0:
            agree += 1
        elif not bullish and not bearish and abs(pct) < 3:
            agree += 1
    return {"scored": n, "agreement_pct": round(agree / n * 100.0, 0)}


# ---- I/O ------------------------------------------------------------------


def _read() -> dict[str, list[dict[str, Any]]]:
    if not _STORE.exists():
        return {}
    try:
        return json.loads(_STORE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, list[dict[str, Any]]]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STORE)


def history(ticker: str) -> list[dict[str, Any]]:
    return _read().get(ticker.upper(), [])


def record_if_changed(
    ticker: str, status: str, confidence: float, reasons: list[str], price: float | None = None
) -> bool:
    """Append an entry iff it's a material change. Returns True if logged."""
    data = _read()
    tk = ticker.upper()
    entries = data.get(tk, [])
    prev = entries[-1] if entries else None
    if not should_log(prev, status, confidence):
        return False
    entries.append(make_entry(tk, status, confidence, reasons, price))
    data[tk] = entries
    _write(data)
    return True


def store_path() -> Path:
    return _STORE
