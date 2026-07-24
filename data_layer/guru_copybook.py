from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class GuruSymbolMeta:
    guru_score: int
    guru_sources: list[str]
    guru_styles: list[str]
    guru_evidence: list[dict[str, Any]]
    guru_thesis: str
    guru_risk: str
    is_high_vol_guru_pick: bool
    sector_theme: str


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):.2f}".rstrip("0").rstrip(".")
    except Exception:
        return ""


def _evidence_label(e: dict[str, Any]) -> str:
    person = str(e.get("person") or "").strip()
    manager = str(e.get("manager") or "").strip()
    pct = _fmt_pct(e.get("weight_pct"))
    who = person or manager or "作业池"
    if pct:
        return f"{who} {pct}%"
    return who


def load_guru_watchlist(path: Path | None = None) -> dict[str, Any]:
    p = path or (ROOT / "config" / "guru_watchlist.yaml")
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def build_guru_symbol_meta(path: Path | None = None) -> dict[str, GuruSymbolMeta]:
    raw = load_guru_watchlist(path)
    sections = raw.get("sectors") or []
    out: dict[str, GuruSymbolMeta] = {}
    for sec in sections:
        theme = str((sec or {}).get("theme") or "未分类")
        gurus = sec.get("gurus") or []
        sources = [
            str((g or {}).get("manager") or (g or {}).get("name") or "").strip()
            for g in gurus
        ]
        styles = [str((g or {}).get("style") or "").strip() for g in gurus]
        symbols = sec.get("symbols") or []
        for row in symbols:
            sym = str((row or {}).get("symbol") or "").upper().strip()
            if not sym:
                continue
            first_guru = gurus[0] if gurus else {}
            evidence = {
                "manager": str((row or {}).get("manager") or (first_guru or {}).get("manager") or (first_guru or {}).get("name") or "").strip(),
                "person": str((row or {}).get("person") or (first_guru or {}).get("person") or "").strip(),
                "quarter": str((row or {}).get("quarter") or (first_guru or {}).get("quarter") or "").strip(),
                "holding_date": str((row or {}).get("holding_date") or (first_guru or {}).get("holding_date") or "").strip(),
                "filing_date": str((row or {}).get("filing_date") or (first_guru or {}).get("filing_date") or "").strip(),
                "source_type": str((row or {}).get("source_type") or (first_guru or {}).get("source_type") or "13F").strip(),
                "weight_pct": (row or {}).get("weight_pct"),
                "position_type": str((row or {}).get("position_type") or "stock").strip(),
                "theme": theme,
            }
            base = int((row or {}).get("base_score") or 0)
            top10 = bool((row or {}).get("top10") or False)
            multi = bool((row or {}).get("multi_guru") or False)
            add = bool((row or {}).get("added") or False)
            new_pos = bool((row or {}).get("new_position") or False)
            stale = bool((row or {}).get("stale_90d") or False)
            high_vol = bool((row or {}).get("high_vol") or False)
            option_like = bool((row or {}).get("option_like") or False)
            score = base
            if top10:
                score += 2
            if multi:
                score += 2
            if add:
                score += 1
            if new_pos:
                score += 1
            if high_vol:
                score -= 1
            if option_like:
                score -= 1
            if stale:
                score -= 1
            prev = out.get(sym)
            prev_score = prev.guru_score if prev else 0
            prev_sources = prev.guru_sources if prev else []
            prev_styles = prev.guru_styles if prev else []
            prev_evidence = prev.guru_evidence if prev else []
            prev_thesis = prev.guru_thesis if prev else ""
            out[sym] = GuruSymbolMeta(
                guru_score=score + prev_score,
                guru_sources=list(dict.fromkeys([*prev_sources, *[s for s in sources if s]])),
                guru_styles=list(dict.fromkeys([*prev_styles, *[s for s in styles if s]])),
                guru_evidence=[*prev_evidence, evidence],
                guru_thesis=str((row or {}).get("thesis") or prev_thesis or str((sec or {}).get("notes") or "")),
                guru_risk=str((row or {}).get("risk") or "13F 有滞后，且不含完整对冲信息。"),
                is_high_vol_guru_pick=high_vol or (prev.is_high_vol_guru_pick if prev else False),
                sector_theme=theme,
            )
    # Backward compatibility for the old simple `tickers: [{symbol, source}]` shape.
    for row in raw.get("tickers") or []:
        sym = str((row or {}).get("symbol") if isinstance(row, dict) else row).upper().strip()
        if not sym or sym in out:
            continue
        source = str((row or {}).get("source") if isinstance(row, dict) else "guru_pool").strip()
        out[sym] = GuruSymbolMeta(
            guru_score=1,
            guru_sources=[source] if source else [],
            guru_styles=[],
            guru_evidence=[{"manager": source, "person": "", "weight_pct": None, "quarter": "", "source_type": "manual"}],
            guru_thesis="手工加入的大佬/主题观察池。",
            guru_risk="手工观察池没有精确 13F 占比；仅作为异动雷达来源。",
            is_high_vol_guru_pick=False,
            sector_theme="手工观察",
        )
    return out


def guru_evidence_label(meta: GuruSymbolMeta, limit: int = 2) -> str:
    labels = [_evidence_label(e) for e in meta.guru_evidence[:limit]]
    return " / ".join([x for x in labels if x])
