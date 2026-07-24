"""FastAPI bridge — serves the real ThesisWatch engine as JSON for the iOS UI."""
from __future__ import annotations

import copy
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_models import Envelope, Mode

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks import exposure as exposure_mod  # noqa: E402
from tasks import thesis_scan  # noqa: E402
from tasks import willow_agent  # noqa: E402

app = FastAPI(title="ThesisWatch API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

logger = logging.getLogger(__name__)

_STATUS_MAP = {"no_action": "no_material_change", "watch": "watch", "re_evaluate": "re_evaluate"}
_NAME = {
    "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet", "AAPL": "Apple",
    "QQQ": "Invesco QQQ Trust", "VOO": "Vanguard S&P 500", "AVGO": "Broadcom",
    "AMZN": "Amazon", "META": "Meta Platforms", "TSLA": "Tesla",
}

_STATE_TO_DATA_STATE = {
    "ok": "ok",
    "stale": "stale_data",
    "partial": "partial_data",
    "unavailable": "offline",
}

_DEMO_TODAY = {
    "date": "2026-07-24",
    "overallStatus": "watch",
    "needsAttention": [
        {
            "ticker": "NVDA", "name": "NVIDIA", "conviction": 64, "prevConviction": 71,
            "status": "re_evaluate", "confidence": "Medium", "coveragePct": 74,
            "positionSize": "large", "horizonMonths": 18, "price": 172.3,
            "dayChangePct": -2.1, "currency": "USD", "weightPct": 19,
            "priceObservedAt": "2026-07-24T20:10:00Z", "dataState": "ok", "cardState": "default",
        }
    ],
    "worthWatching": [
        {
            "ticker": "MSFT", "name": "Microsoft", "conviction": 78, "prevConviction": 71,
            "status": "watch", "confidence": "Medium-high", "coveragePct": 82,
            "positionSize": "moderate", "horizonMonths": 18, "price": 462.11,
            "dayChangePct": 0.4, "currency": "USD", "weightPct": 21,
            "priceObservedAt": "2026-07-24T20:10:00Z", "dataState": "ok", "cardState": "default",
        }
    ],
    "noMaterialChange": [],
    "updatedAgoMinutes": 18,
    "dataState": "ok",
}

_DEMO_WHY = {
    "MSFT": {
        "ticker": "MSFT", "prevConviction": 71, "currentConviction": 78,
        "status": "watch", "asOf": "2026-07-24T19:52:00Z", "dataState": "ok",
        "drivers": [
            {"label": "Azure guidance improved", "points": 4, "sign": "positive"},
            {"label": "AI capex uncertainty", "points": -2, "sign": "negative"},
        ],
        "meaningForYou": "The thesis strengthened but capex risk remains.",
    }
}

_DEMO_EVIDENCE = {
    "MSFT": [
        {
            "id": "ev-101", "claim": "Azure growth remained above expectations", "type": "fact",
            "sourceName": "Demo transcript", "observedAt": "2026-07-22T21:00:00Z",
            "fetchedAt": "2026-07-24T19:09:00Z", "freshness": "fresh", "interpretation": None,
            "confidence": "High", "thesisClaimId": "MSFT-fundamental", "sign": "positive",
            "qualityNote": None,
        }
    ]
}

_DEMO_THESIS = {
    "MSFT": {
        "ticker": "MSFT", "status": "watch", "oneLiner": "Thesis strengthened this week",
        "conviction": 78, "confidence": "Medium-high", "coveragePct": 82, "horizonMonths": 18,
        "dataState": "ok", "asOf": "2026-07-24T19:52:00Z",
        "whatChanged": {
            "d1": [{"label": "Azure guidance improved", "points": 4, "sign": "positive"}],
            "w1": [{"label": "Azure guidance improved", "points": 4, "sign": "positive"}],
            "m1": [{"label": "Azure guidance improved", "points": 4, "sign": "positive"}],
        },
        "currentThesis": [{"id": "MSFT-0", "text": "AI demand supports cloud growth."}],
        "supporting": [],
        "risks": [],
        "whatWouldChangeMyMind": ["Azure growth below 20% for two quarters"],
        "catalysts": [{"date": "2026-10-28", "label": "FY26 Q1 earnings"}],
        "decisionHistory": [],
        "conflicts": [],
    }
}

_DEMO_EXPOSURES = [
    {"factor": "AI infrastructure", "level": "high", "holdings": [{"ticker": "MSFT", "weightPct": 21}]}
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_observed_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        local = datetime.strptime(value, "%Y-%m-%d %H:%M")
        return local.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    except ValueError:
        return None


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


def _advice() -> dict[str, Any]:
    return willow_agent.build_advice()


def _derive_state(data: Any, warnings: list[str], *, stale: bool = False) -> str:
    if data is None:
        return "unavailable"
    if stale:
        return "stale"
    if warnings:
        return "partial"
    return "ok"


def _mk_envelope(
    *,
    data: Any,
    mode: Mode,
    warnings: list[str],
    sources: list[str],
    observed_at: datetime | None,
    fetched_at: datetime,
    stale: bool = False,
) -> Envelope[Any]:
    state = _derive_state(data, warnings, stale=stale)
    if isinstance(data, dict):
        data["dataState"] = _STATE_TO_DATA_STATE[state]
    return Envelope[Any](
        data=data,
        state=state,
        mode=mode,
        observed_at=observed_at,
        fetched_at=fetched_at,
        sources=sources,
        warnings=warnings,
    )


def _holding(
    stock: dict[str, Any],
    focus: dict[str, Any] | None,
    status_obj: Any,
    *,
    price_observed_at: datetime | None,
    warnings: list[str],
) -> dict[str, Any]:
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load conviction history for %s", tk)
        warnings.append(f"conviction history unavailable for {tk}: {exc}")
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
        "priceObservedAt": price_observed_at.isoformat() if price_observed_at else None,
        "dataState": "ok" if not stock.get("degraded") else "partial_data",
        "cardState": "default",
    }


def _overall_from_holdings(holdings: list[dict[str, Any]]) -> str:
    if any(h["status"] == "re_evaluate" for h in holdings):
        return "re_evaluate"
    if any(h["status"] == "watch" for h in holdings):
        return "watch"
    return "no_action"


def _conf_from_evidence(kind: str) -> str:
    return {"fundamental": "High", "price": "High", "macro": "Medium",
            "news": "Medium", "smart_money": "Medium", "user": "Low"}.get(kind, "Medium")


def _sign(delta: float) -> str:
    return "positive" if delta > 0 else "negative" if delta < 0 else "neutral"


def _evidence_type(kind: str, stance: str) -> str:
    if stance == "counter":
        return "counterargument"
    if kind in {"price", "fundamental", "macro"}:
        return "fact"
    if kind == "user":
        return "assumption"
    return "model_interpretation"


def _evidence_freshness(age_days: float | None, conflict: bool, is_counter: bool) -> str:
    if conflict and is_counter:
        return "conflicting"
    if age_days is None:
        return "unavailable"
    if age_days <= 45:
        return "fresh"
    return "stale"


def _compute_why_live(
    ticker: str,
    advice: dict[str, Any],
    *,
    observed_at: datetime | None,
    warnings: list[str],
    sources: list[str],
) -> dict[str, Any]:
    tk = ticker.upper()
    focus = next((f for f in advice.get("focus", []) if f["ticker"].upper() == tk), None)
    conv = int(round(float((focus or {}).get("conviction", 50))))
    prev = conv
    try:
        from tasks import conviction_history as ch  # noqa: PLC0415

        hist = ch.history(tk)
        before = ch.latest_before(hist, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        if before:
            prev = int(round(float(before.get("conviction", conv))))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load conviction history for %s", tk)
        warnings.append(f"conviction history unavailable for {tk}: {exc}")

    drivers = []
    for fac in sorted((focus or {}).get("factors", []), key=lambda f: abs(f.get("delta", 0)), reverse=True):
        d = float(fac.get("delta", 0) or 0)
        drivers.append({
            "label": f"{fac.get('label', '')}（{fac.get('detail', '')}）",
            "points": int(round(d)),
            "sign": _sign(d),
        })

    status = "no_material_change"
    meaning = ""
    try:
        _, statuses = thesis_scan.scan_portfolio(advice, persist=False)
        st = next((s for s in statuses if s.ticker.upper() == tk), None)
        status = _STATUS_MAP.get(getattr(st, "status", "no_action"), "no_material_change")
        meaning = ("；".join(getattr(st, "reasons", [])[:2]) or "仅价格波动，核心逻辑未变。") if st else ""
        sources.append("tasks.thesis_scan.scan_portfolio")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to scan portfolio for %s", tk)
        warnings.append(f"scan failed for {tk}: {exc}")

    if observed_at is None:
        warnings.append(f"observed_at unknown for {tk} why")

    return {
        "ticker": tk,
        "prevConviction": prev,
        "currentConviction": conv,
        "status": status,
        "asOf": observed_at.isoformat() if observed_at else None,
        "dataState": "ok",
        "drivers": drivers,
        "meaningForYou": meaning,
    }


def _compute_evidence_live(
    ticker: str,
    advice: dict[str, Any],
    *,
    fetched_at: datetime,
    warnings: list[str],
    sources: list[str],
) -> tuple[list[dict[str, Any]], datetime | None]:
    from tasks import thesis_autoevidence, thesis_store  # noqa: PLC0415
    from tasks.thesis import InvestmentThesis  # noqa: PLC0415

    tk = ticker.upper()
    stock = next((s for s in advice.get("stocks", []) if s["ticker"].upper() == tk), None)
    focus = next((f for f in advice.get("focus", []) if f["ticker"].upper() == tk), None)
    news_tk = {str(i.get("ticker", "")).upper() for i in (advice.get("news", {}) or {}).get("holdings", [])}
    flags = (advice.get("portfolio", {}) or {}).get("concentration_flags", []) or []
    auto = thesis_autoevidence.evidence_from_signals(stock, focus, None, tk in news_tk, flags)
    base = thesis_store.load_thesis(tk) or InvestmentThesis(ticker=tk)
    merged = InvestmentThesis(
        ticker=tk,
        claims=base.claims,
        catalysts=base.catalysts,
        risks=base.risks,
        invalidation_conditions=base.invalidation_conditions,
        evidence=[e for e in base.evidence if not str(e.source).startswith("auto:")] + auto,
    )

    support_w = sum(e.weight for e in merged.evidence if e.stance == "support")
    counter_w = sum(e.weight for e in merged.evidence if e.stance == "counter")
    conflict = support_w > 0 and counter_w > 0

    out: list[dict[str, Any]] = []
    observed_values: list[datetime] = []
    missing_observed = 0
    for idx, e in enumerate(merged.evidence, start=1):
        observed_dt = _parse_observed_at(e.observed_at)
        if observed_dt:
            observed_values.append(observed_dt)
        else:
            missing_observed += 1
        age_days = e.age_days(now=fetched_at)
        freshness = _evidence_freshness(age_days, conflict, e.stance == "counter")
        quality_note = None
        if freshness == "stale":
            quality_note = f"{age_days:.0f} days old"
        elif freshness == "unavailable":
            quality_note = "missing observed timestamp"
        elif freshness == "conflicting":
            quality_note = "counter evidence present with support evidence"

        out.append({
            "id": f"ev-{tk}-{idx}",
            "claim": e.text,
            "type": _evidence_type(e.kind, e.stance),
            "sourceName": e.source or "ThesisWatch",
            "observedAt": observed_dt.isoformat() if observed_dt else None,
            "fetchedAt": None,
            "freshness": freshness,
            "interpretation": None,
            "confidence": _conf_from_evidence(e.kind),
            "thesisClaimId": f"{tk}-{e.kind}",
            "sign": "positive" if e.stance == "support" else "negative",
            "qualityNote": quality_note,
        })

    if missing_observed:
        warnings.append(f"observed_at unknown for {missing_observed} {tk} evidence items")
    warnings.append(f"fetchedAt unknown for {tk} evidence items")
    if not observed_values:
        warnings.append(f"observed_at unknown for {tk} evidence")

    return out, (max(observed_values) if observed_values else None)


@app.get("/api/today", response_model=Envelope[dict[str, Any]])
def today(mode: Mode = "live") -> Envelope[dict[str, Any]]:
    fetched_at = _utc_now()
    if mode == "demo":
        observed_at = _parse_observed_at("2026-07-24T20:10:00Z")
        return _mk_envelope(
            data=copy.deepcopy(_DEMO_TODAY),
            mode=mode,
            warnings=[],
            sources=["demo.sample"],
            observed_at=observed_at,
            fetched_at=fetched_at,
        )

    warnings: list[str] = []
    sources = ["tasks.willow_agent.build_advice"]
    try:
        advice = _advice()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build advice for /api/today")
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"build_advice failed: {exc}"],
            sources=sources,
            observed_at=None,
            fetched_at=fetched_at,
        )

    observed_at = _parse_observed_at(str(advice.get("as_of") or ""))
    if observed_at is None:
        warnings.append("observed_at unknown for today snapshot")

    statuses: list[Any] = []
    overall = "no_action"
    try:
        ps, statuses = thesis_scan.scan_portfolio(advice, persist=False)
        overall = ps.overall
        sources.append("tasks.thesis_scan.scan_portfolio")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to run thesis scan for /api/today")
        warnings.append(f"thesis scan unavailable: {exc}")

    by_tk = {s.ticker.upper(): s for s in statuses}
    focus_by = {f["ticker"].upper(): f for f in advice.get("focus", [])}

    holdings = []
    for s in advice.get("stocks", []):
        tk = s["ticker"].upper()
        holdings.append(_holding(s, focus_by.get(tk), by_tk.get(tk), price_observed_at=observed_at, warnings=warnings))

    if not statuses and holdings:
        overall = _overall_from_holdings(holdings)

    def bucket(name: str) -> list[dict[str, Any]]:
        return [h for h in holdings if h["status"] == name]

    if observed_at is None:
        updated_ago_minutes = None
    else:
        updated_ago_minutes = max(0, int((fetched_at - observed_at).total_seconds() // 60))

    data = {
        "date": (observed_at or fetched_at).date().isoformat(),
        "overallStatus": overall,
        "needsAttention": bucket("re_evaluate"),
        "worthWatching": bucket("watch"),
        "noMaterialChange": bucket("no_material_change"),
        "updatedAgoMinutes": updated_ago_minutes,
        "dataState": "ok",
    }

    stale = False
    if observed_at is not None:
        stale = (fetched_at - observed_at) > timedelta(hours=24)

    return _mk_envelope(
        data=data if holdings else None,
        mode=mode,
        warnings=warnings,
        sources=sources,
        observed_at=observed_at,
        fetched_at=fetched_at,
        stale=stale,
    )


@app.get("/api/exposures", response_model=Envelope[list[dict[str, Any]]])
def exposures(mode: Mode = "live") -> Envelope[list[dict[str, Any]]]:
    fetched_at = _utc_now()
    if mode == "demo":
        return _mk_envelope(
            data=copy.deepcopy(_DEMO_EXPOSURES),
            mode=mode,
            warnings=[],
            sources=["demo.sample"],
            observed_at=_parse_observed_at("2026-07-24T20:10:00Z"),
            fetched_at=fetched_at,
        )

    warnings: list[str] = []
    sources = ["tasks.willow_agent.build_advice", "tasks.exposure.compute_exposures"]
    try:
        advice = _advice()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build advice for /api/exposures")
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"build_advice failed: {exc}"],
            sources=sources,
            observed_at=None,
            fetched_at=fetched_at,
        )

    observed_at = _parse_observed_at(str(advice.get("as_of") or ""))
    if observed_at is None:
        warnings.append("observed_at unknown for exposures")

    try:
        positions = [
            {"ticker": s["ticker"], "weight_pct": float(s.get("weight_pct") or 0.0)}
            for s in advice.get("stocks", []) if (s.get("weight_pct") or 0) > 0
        ]
        out = []
        for e in exposure_mod.compute_exposures(positions):
            out.append({
                "factor": e.theme,
                "level": {"高": "high", "中": "medium", "低": "low"}.get(e.level, "low"),
                "holdings": [
                    {"ticker": c["ticker"], "weightPct": int(round(c["weight_pct"]))}
                    for c in e.contributors
                ],
            })
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to compute exposures")
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"exposure compute failed: {exc}"],
            sources=sources,
            observed_at=observed_at,
            fetched_at=fetched_at,
        )

    return _mk_envelope(
        data=out,
        mode=mode,
        warnings=warnings,
        sources=sources,
        observed_at=observed_at,
        fetched_at=fetched_at,
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "thesiswatch"}


@app.get("/api/why/{ticker}", response_model=Envelope[dict[str, Any]])
def why(ticker: str, mode: Mode = "live") -> Envelope[dict[str, Any]]:
    fetched_at = _utc_now()
    if mode == "demo":
        tk = ticker.upper()
        data = copy.deepcopy(_DEMO_WHY.get(tk) or {
            "ticker": tk,
            "prevConviction": 50,
            "currentConviction": 50,
            "drivers": [],
            "meaningForYou": "Demo mode sample.",
            "status": "no_material_change",
            "asOf": "2026-07-24T19:52:00Z",
            "dataState": "ok",
        })
        return _mk_envelope(
            data=data,
            mode=mode,
            warnings=[],
            sources=["demo.sample"],
            observed_at=_parse_observed_at(data.get("asOf")),
            fetched_at=fetched_at,
        )

    warnings: list[str] = []
    sources = ["tasks.willow_agent.build_advice"]
    try:
        advice = _advice()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build advice for /api/why/%s", ticker)
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"build_advice failed: {exc}"],
            sources=sources,
            observed_at=None,
            fetched_at=fetched_at,
        )

    observed_at = _parse_observed_at(str(advice.get("as_of") or ""))
    data = _compute_why_live(ticker, advice, observed_at=observed_at, warnings=warnings, sources=sources)
    return _mk_envelope(
        data=data,
        mode=mode,
        warnings=warnings,
        sources=sources,
        observed_at=observed_at,
        fetched_at=fetched_at,
    )


@app.get("/api/evidence/{ticker}", response_model=Envelope[list[dict[str, Any]]])
def evidence(ticker: str, mode: Mode = "live") -> Envelope[list[dict[str, Any]]]:
    fetched_at = _utc_now()
    if mode == "demo":
        tk = ticker.upper()
        data = copy.deepcopy(_DEMO_EVIDENCE.get(tk) or [])
        observed_values = [
            _parse_observed_at(item.get("observedAt"))
            for item in data
            if _parse_observed_at(item.get("observedAt")) is not None
        ]
        observed_at = max(observed_values) if observed_values else None
        return _mk_envelope(
            data=data,
            mode=mode,
            warnings=[],
            sources=["demo.sample"],
            observed_at=observed_at,
            fetched_at=fetched_at,
        )

    warnings: list[str] = []
    sources = ["tasks.willow_agent.build_advice", "tasks.thesis_store.load_thesis", "tasks.thesis_autoevidence"]
    try:
        advice = _advice()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build advice for /api/evidence/%s", ticker)
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"build_advice failed: {exc}"],
            sources=sources,
            observed_at=None,
            fetched_at=fetched_at,
        )

    try:
        data, observed_at = _compute_evidence_live(ticker, advice, fetched_at=fetched_at, warnings=warnings, sources=sources)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build evidence for %s", ticker)
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"evidence build failed: {exc}"],
            sources=sources,
            observed_at=None,
            fetched_at=fetched_at,
        )

    stale = any(item.get("freshness") == "stale" for item in data) and not warnings
    return _mk_envelope(
        data=data,
        mode=mode,
        warnings=warnings,
        sources=sources,
        observed_at=observed_at,
        fetched_at=fetched_at,
        stale=stale,
    )


@app.get("/api/thesis/{ticker}", response_model=Envelope[dict[str, Any]])
def thesis(ticker: str, mode: Mode = "live") -> Envelope[dict[str, Any]]:
    fetched_at = _utc_now()
    tk = ticker.upper()
    if mode == "demo":
        data = copy.deepcopy(_DEMO_THESIS.get(tk) or {
            "ticker": tk,
            "status": "no_material_change",
            "oneLiner": "Demo mode sample.",
            "conviction": 50,
            "confidence": "Medium",
            "coveragePct": 0,
            "whatChanged": {"d1": [], "w1": [], "m1": []},
            "currentThesis": [],
            "supporting": [],
            "risks": [],
            "whatWouldChangeMyMind": [],
            "catalysts": [],
            "decisionHistory": [],
            "conflicts": [],
            "horizonMonths": 18,
            "dataState": "ok",
            "asOf": "2026-07-24T19:52:00Z",
        })
        return _mk_envelope(
            data=data,
            mode=mode,
            warnings=[],
            sources=["demo.sample"],
            observed_at=_parse_observed_at(data.get("asOf")),
            fetched_at=fetched_at,
        )

    warnings: list[str] = []
    sources = ["tasks.willow_agent.build_advice", "tasks.thesis_store.load_thesis"]
    try:
        advice = _advice()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build advice for /api/thesis/%s", ticker)
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"build_advice failed: {exc}"],
            sources=sources,
            observed_at=None,
            fetched_at=fetched_at,
        )

    observed_at = _parse_observed_at(str(advice.get("as_of") or ""))
    if observed_at is None:
        warnings.append(f"observed_at unknown for {tk} thesis")

    try:
        from tasks import decision_history as dh, thesis_store  # noqa: PLC0415
        from tasks.thesis import InvestmentThesis  # noqa: PLC0415

        focus = next((f for f in advice.get("focus", []) if f["ticker"].upper() == tk), None)
        conv = int(round(float((focus or {}).get("conviction", 50))))
        t = thesis_store.load_thesis(tk) or InvestmentThesis(ticker=tk)
        c = t.confidence()

        why_data = _compute_why_live(tk, advice, observed_at=observed_at, warnings=warnings, sources=sources)
        ev_data, ev_observed = _compute_evidence_live(tk, advice, fetched_at=fetched_at, warnings=warnings, sources=sources)

        status = why_data["status"]
        one = {
            "re_evaluate": "论点需要重新评估",
            "watch": "有新变化，留意",
            "no_material_change": "无实质变化",
        }.get(status, "无实质变化")

        try:
            hist = [{
                "date": h["at"][:10],
                "from": int(round(h.get("confidence", conv))),
                "to": int(round(h.get("confidence", conv))),
                "note": "；".join(h.get("reasons", [])[:1]),
            } for h in dh.history(tk)][-8:]
            sources.append("tasks.decision_history.history")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load decision history for %s", tk)
            warnings.append(f"decision history unavailable for {tk}: {exc}")
            hist = []

        data = {
            "ticker": tk,
            "status": status,
            "oneLiner": one,
            "conviction": conv,
            "confidence": _conf_label(conv),
            "coveragePct": int(round(c.get("coverage", 0) * 100)),
            "horizonMonths": 18,
            "dataState": "ok",
            "asOf": observed_at.isoformat() if observed_at else None,
            "whatChanged": {
                "d1": why_data["drivers"],
                "w1": why_data["drivers"],
                "m1": why_data["drivers"],
            },
            "currentThesis": [{"id": f"{tk}-{i}", "text": cl} for i, cl in enumerate(t.claims)],
            "supporting": [e for e in ev_data if e["sign"] == "positive"],
            "risks": [e for e in ev_data if e["sign"] == "negative"],
            "whatWouldChangeMyMind": t.invalidation_conditions,
            "catalysts": [{"date": "", "label": item} for item in t.catalysts],
            "decisionHistory": hist,
            "conflicts": [],
        }

        observed_candidates = [d for d in [observed_at, ev_observed] if d is not None]
        envelope_observed = max(observed_candidates) if observed_candidates else None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build thesis for %s", ticker)
        return _mk_envelope(
            data=None,
            mode=mode,
            warnings=[f"thesis build failed for {tk}: {exc}"],
            sources=sources,
            observed_at=observed_at,
            fetched_at=fetched_at,
        )

    return _mk_envelope(
        data=data,
        mode=mode,
        warnings=warnings,
        sources=sources,
        observed_at=envelope_observed,
        fetched_at=fetched_at,
    )
