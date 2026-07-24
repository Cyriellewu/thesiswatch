"""Thesis Ledger — structured investment-thesis data model.

The core of the "evidence-backed thesis monitor" positioning. Instead of an LLM
free-writing a paragraph, a thesis is a *structured* object: claims, catalysts,
risks, explicit invalidation conditions, and dated evidence — each piece with a
source. From that structure we can compute calibrated confidence, evidence
coverage, freshness, and (in thesis_delta.py) what changed since last time.

Pure and offline: no I/O, no LLM, no network. Fully unit-tested. Persistence
lives in thesis_store.py; diffing in thesis_delta.py.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Stance = Literal["support", "counter"]
EvidenceKind = Literal["price", "news", "fundamental", "smart_money", "macro", "user"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Evidence:
    """One dated, sourced data point bearing on the thesis."""
    text: str
    kind: EvidenceKind
    stance: Stance = "support"          # does it support or counter the thesis
    source: str = ""                    # url or provider label
    weight: float = 1.0                 # relative importance (0..3)
    observed_at: str = field(default_factory=_now_iso)

    @property
    def id(self) -> str:
        raw = f"{self.kind}|{self.stance}|{self.text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def age_days(self, *, now: datetime | None = None) -> float | None:
        obs = _parse_iso(self.observed_at)
        if obs is None:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - obs).total_seconds() / 86400.0)


@dataclass
class InvestmentThesis:
    ticker: str
    claims: list[str] = field(default_factory=list)           # why I own it
    catalysts: list[str] = field(default_factory=list)        # what could prove it
    risks: list[str] = field(default_factory=list)            # what could break it
    invalidation_conditions: list[str] = field(default_factory=list)  # explicit kill switches
    evidence: list[Evidence] = field(default_factory=list)
    horizon: str = "12-24 months"
    updated_at: str = field(default_factory=_now_iso)

    # ---- derived, calibrated metrics -------------------------------------

    def coverage(self) -> float:
        """0..1 — how complete the thesis is (has claims, catalysts, risks,
        invalidation, and at least some evidence). A thesis with only a claim
        and no risks/invalidation is honestly incomplete."""
        parts = [
            bool(self.claims),
            bool(self.catalysts),
            bool(self.risks),
            bool(self.invalidation_conditions),
            bool(self.evidence),
        ]
        return round(sum(parts) / len(parts), 2)

    def freshness_days(self, *, now: datetime | None = None) -> float | None:
        """Age (days) of the most recent piece of evidence; None if no evidence."""
        ages = [e.age_days(now=now) for e in self.evidence]
        ages = [a for a in ages if a is not None]
        return round(min(ages), 2) if ages else None

    def net_evidence(self) -> float:
        """Signed sum of evidence weights (support positive, counter negative)."""
        total = 0.0
        for e in self.evidence:
            total += e.weight if e.stance == "support" else -e.weight
        return round(total, 2)

    def confidence(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Calibrated confidence — NEVER a bare number. Returns a level plus the
        drivers (coverage, freshness, evidence balance) so false precision is
        impossible."""
        cov = self.coverage()
        fresh = self.freshness_days(now=now)
        support = sum(e.weight for e in self.evidence if e.stance == "support")
        counter = sum(e.weight for e in self.evidence if e.stance == "counter")
        total = support + counter
        balance = (support / total) if total > 0 else 0.5  # 0..1, 0.5 = neutral

        # Base score from evidence balance, scaled by how complete + fresh it is.
        score = 50.0 + (balance - 0.5) * 100.0        # -50..+50 around 50
        score *= 0.5 + 0.5 * cov                       # incomplete theses can't be confident
        if fresh is not None and fresh > 30:
            score = 50.0 + (score - 50.0) * 0.7        # stale evidence dampens confidence
        score = round(max(0.0, min(100.0, score)), 1)

        if total == 0 or cov < 0.4:
            level = "unknown"
        elif score >= 66:
            level = "high"
        elif score >= 45:
            level = "medium"
        else:
            level = "low"

        return {
            "score": score,
            "level": level,
            "coverage": cov,
            "freshness_days": fresh,
            "support_weight": round(support, 2),
            "counter_weight": round(counter, 2),
        }

    def invalidation_hits(self, signals: dict[str, Any]) -> list[str]:
        """Return invalidation conditions that are *literally* triggered by a
        signals dict. Only exact keyword matches count — we never guess that a
        thesis is broken; the condition text must reference a signal we can
        check. `signals` maps a lowercase keyword -> truthy when triggered."""
        hits: list[str] = []
        for cond in self.invalidation_conditions:
            low = cond.lower()
            for key, triggered in signals.items():
                if triggered and str(key).lower() in low:
                    hits.append(cond)
                    break
        return hits

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "InvestmentThesis":
        ev = [Evidence(**e) for e in (d.get("evidence") or [])]
        return InvestmentThesis(
            ticker=str(d.get("ticker", "")).upper(),
            claims=list(d.get("claims") or []),
            catalysts=list(d.get("catalysts") or []),
            risks=list(d.get("risks") or []),
            invalidation_conditions=list(d.get("invalidation_conditions") or []),
            evidence=ev,
            horizon=d.get("horizon", "12-24 months"),
            updated_at=d.get("updated_at", _now_iso()),
        )
