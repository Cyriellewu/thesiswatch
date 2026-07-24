"""FastAPI bridge — serves the real ThesisWatch engine as JSON for the iOS UI.

Maps the Python engine (build_advice + thesis_scan + exposure + thesis_explain
+ decision_history + conviction_history) into the exact TypeScript contracts the
React app consumes (see app/src/types.ts). This is what turns the pretty mobile
prototype into one real website.

Run:  uvicorn api_server:app --port 8000   (ALPHAWATCH_OFFLINE=1 for demo)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks import willow_agent  # noqa: E402
from tasks import thesis_scan  # noqa: E402
from tasks import exposure as exposure_mod  # noqa: E402

app = FastAPI(title="ThesisWatch API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_STATUS_MAP = {"no_action": "no_material_change", "watch": "watch", "re_evaluate": "re_evaluate"}
_NAME = {
    "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet", "AAPL": "Apple",
    "QQQ": "Invesco QQQ Trust", "VOO": "Vanguard S&P 500", "AVGO": "Broadcom",
    "AMZN": "Amazon", "META": "Meta Platforms", "TSLA": "Tesla",
}


def _conf_label(score: float) -> str:
    if score >= 75:
        return "High"
    if score >= 62:
        return "Medium-high"
    if score >= 45:
        return "Medium"
    return "Low"


def _pos_size(weight: float) -> str:
    return "large" if weight >= 18 else "moderate" if weight >= 8 else "small"


def _advice():
    return willow_agent.build_advice()


def _holding(stock: dict[str, Any], focus: dict[str, Any] | None, status_obj) -> dict[str, Any]:
    tk = stock["ticker"]
    conv = float((focus or {}).get("conviction", 50.0))
    prev = conv
    try:
        from tasks import conviction_history as ch  # noqa: PLC0415

        hist = ch.history(tk)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        before = ch.latest_before(hist, today)
        if before:
            prev = float(before.get("conviction", conv))
    except Exception:
        pass
    cov = int(round((status_obj.confidence.get("coverage", 0.0) if status_obj else 0.0) * 100))
    return {
        "ticker": tk,
        "name": _NAME.get(tk, tk),
        "conviction": int(round(conv)),
        "prevConviction": int(round(prev)),
        "status": _STATUS_MAP.get(getattr(status_obj, "status", "no_action"), "no_material_change"),
        "confidence": _conf_label(conv),
        "coveragePct": cov,
        "positionSize": _pos_size(float(stock.get("weight_pct") or 0)),
        "horizonMonths": 18,
        "price": round(float(stock.get("px") or 0.0), 2),
        "dayChangePct": round(float(stock.get("chg_pct") or 0.0), 2),
        "currency": "USD",
        "weightPct": int(round(float(stock.get("weight_pct") or 0))),
        "priceObservedAt": datetime.now(timezone.utc).isoformat(),
        "dataState": "ok" if not stock.get("degraded") else "partial_data",
        "cardState": "default",
    }


@app.get("/api/today")
def today() -> dict[str, Any]:
    advice = _advice()
    ps, statuses = thesis_scan.scan_portfolio(advice, persist=False)
    by_tk = {s.ticker.upper(): s for s in statuses}
    focus_by = {f["ticker"].upper(): f for f in advice.get("focus", [])}

    holdings = []
    for s in advice.get("stocks", []):
        tk = s["ticker"].upper()
        holdings.append(_holding(s, focus_by.get(tk), by_tk.get(tk)))

    def bucket(name: str) -> list[dict[str, Any]]:
        return [h for h in holdings if h["status"] == name]

    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "overallStatus": ps.overall,
        "needsAttention": bucket("re_evaluate"),
        "worthWatching": bucket("watch"),
        "noMaterialChange": bucket("no_material_change"),
        "updatedAgoMinutes": 0,
        "dataState": "ok",
    }


@app.get("/api/exposures")
def exposures() -> list[dict[str, Any]]:
    advice = _advice()
    positions = [
        {"ticker": s["ticker"], "weight_pct": float(s.get("weight_pct") or 0.0)}
        for s in advice.get("stocks", []) if (s.get("weight_pct") or 0) > 0
    ]
    out = []
    for e in exposure_mod.compute_exposures(positions):
        out.append({
            "factor": e.theme,
            "level": {"高": "high", "中": "medium", "低": "low"}.get(e.level, "low"),
            "holdings": [{"ticker": c["ticker"], "weightPct": int(round(c["weight_pct"]))} for c in e.contributors],
        })
    return out


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "thesiswatch"}


def _conf_from_evidence(kind: str) -> str:
    return {"fundamental": "High", "price": "High", "macro": "Medium",
            "news": "Medium", "smart_money": "Medium", "user": "Low"}.get(kind, "Medium")


def _sign(delta: float) -> str:
    return "positive" if delta > 0 else "negative" if delta < 0 else "neutral"


@app.get("/api/why/{ticker}")
def why(ticker: str) -> dict[str, Any]:
    """Why-Changed: conviction drivers from the live conviction factors."""
    advice = _advice()
    tk = ticker.upper()
    focus = next((f for f in advice.get("focus", []) if f["ticker"].upper() == tk), None)
    stock = next((s for s in advice.get("stocks", []) if s["ticker"].upper() == tk), None)
    conv = int(round(float((focus or {}).get("conviction", 50))))
    prev = conv
    try:
        from tasks import conviction_history as ch  # noqa: PLC0415

        hist = ch.history(tk)
        before = ch.latest_before(hist, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        if before:
            prev = int(round(float(before.get("conviction", conv))))
    except Exception:
        pass
    drivers = []
    for fac in sorted((focus or {}).get("factors", []), key=lambda f: abs(f.get("delta", 0)), reverse=True):
        d = float(fac.get("delta", 0) or 0)
        drivers.append({"label": f"{fac.get('label','')}（{fac.get('detail','')}）",
                        "points": int(round(d)), "sign": _sign(d)})
    ps, statuses = thesis_scan.scan_portfolio(advice, persist=False)
    st = next((s for s in statuses if s.ticker.upper() == tk), None)
    status = _STATUS_MAP.get(getattr(st, "status", "no_action"), "no_material_change")
    meaning = ("；".join(getattr(st, "reasons", [])[:2]) or "仅价格波动，核心逻辑未变。") if st else ""
    return {
        "ticker": tk, "prevConviction": prev, "currentConviction": conv, "status": status,
        "asOf": datetime.now(timezone.utc).isoformat(), "dataState": "ok",
        "drivers": drivers, "meaningForYou": meaning,
    }


@app.get("/api/evidence/{ticker}")
def evidence(ticker: str) -> list[dict[str, Any]]:
    """Evidence sheet from the live thesis (auto-evidence + stored)."""
    from tasks import thesis_store, thesis_autoevidence  # noqa: PLC0415
    from tasks.thesis import InvestmentThesis  # noqa: PLC0415
    from tasks.thesis_explain import classify_evidence  # noqa: PLC0415

    advice = _advice()
    tk = ticker.upper()
    stock = next((s for s in advice.get("stocks", []) if s["ticker"].upper() == tk), None)
    focus = next((f for f in advice.get("focus", []) if f["ticker"].upper() == tk), None)
    news_tk = {str(i.get("ticker", "")).upper() for i in (advice.get("news", {}) or {}).get("holdings", [])}
    flags = (advice.get("portfolio", {}) or {}).get("concentration_flags", []) or []
    auto = thesis_autoevidence.evidence_from_signals(stock, focus, None, tk in news_tk, flags)
    base = thesis_store.load_thesis(tk) or InvestmentThesis(ticker=tk)
    merged = InvestmentThesis(ticker=tk, claims=base.claims, catalysts=base.catalysts,
                              risks=base.risks, invalidation_conditions=base.invalidation_conditions,
                              evidence=[e for e in base.evidence if not str(e.source).startswith("auto:")] + auto)
    cls = classify_evidence(merged)
    out = []
    type_map = [("facts", "fact"), ("interpretations", "model_interpretation"),
                ("assumptions", "assumption"), ("counterarguments", "counterargument")]
    idx = 0
    for bucket, etype in type_map:
        for it in cls[bucket]:
            idx += 1
            out.append({
                "id": f"ev-{tk}-{idx}", "claim": it["text"], "type": etype,
                "sourceName": it["source"] or "ThesisWatch", "observedAt": datetime.now(timezone.utc).isoformat(),
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "freshness": "conflicting" if cls["conflict"] and etype == "counterargument" else (
                    "stale" if "过期" in it["freshness"] or "偏旧" in it["freshness"] else "fresh"),
                "interpretation": "", "confidence": _conf_from_evidence(it["kind"]),
                "thesisClaimId": f"{tk}-{it['kind']}", "sign": it["stance"] == "support" and "positive" or "negative",
                "qualityNote": None if "过期" not in it["freshness"] else it["freshness"],
            })
    return out


@app.get("/api/thesis/{ticker}")
def thesis(ticker: str) -> dict[str, Any]:
    """Full Stock Thesis view from the ledger + decision history + why."""
    from tasks import thesis_store, decision_history as dh  # noqa: PLC0415
    from tasks.thesis import InvestmentThesis  # noqa: PLC0415

    advice = _advice()
    tk = ticker.upper()
    focus = next((f for f in advice.get("focus", []) if f["ticker"].upper() == tk), None)
    conv = int(round(float((focus or {}).get("conviction", 50))))
    t = thesis_store.load_thesis(tk) or InvestmentThesis(ticker=tk)
    c = t.confidence()
    whyd = why(tk)
    ps, statuses = thesis_scan.scan_portfolio(advice, persist=False)
    st = next((s for s in statuses if s.ticker.upper() == tk), None)
    status = _STATUS_MAP.get(getattr(st, "status", "no_action"), "no_material_change")
    ev = evidence(tk)
    hist = [{"date": h["at"][:10], "from": int(round(h.get("confidence", conv))),
             "to": int(round(h.get("confidence", conv))), "note": "；".join(h.get("reasons", [])[:1])}
            for h in dh.history(tk)][-8:]
    one = {"re_evaluate": "论点需要重新评估", "watch": "有新变化，留意", "no_material_change": "无实质变化"}[status]
    return {
        "ticker": tk, "status": status, "oneLiner": one, "conviction": conv,
        "confidence": _conf_label(conv), "coveragePct": int(round(c.get("coverage", 0) * 100)),
        "horizonMonths": 18, "dataState": "ok", "asOf": datetime.now(timezone.utc).isoformat(),
        "whatChanged": {"d1": whyd["drivers"], "w1": whyd["drivers"], "m1": whyd["drivers"]},
        "currentThesis": [{"id": f"{tk}-{i}", "text": cl} for i, cl in enumerate(t.claims)],
        "supporting": [e for e in ev if e["sign"] == "positive"],
        "risks": [e for e in ev if e["sign"] == "negative"],
        "whatWouldChangeMyMind": t.invalidation_conditions,
        "catalysts": [{"date": "", "label": c} for c in t.catalysts],
        "decisionHistory": hist, "conflicts": [],
    }
