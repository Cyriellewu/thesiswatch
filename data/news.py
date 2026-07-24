from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class NewsItem:
    ts: str
    title: str
    summary: str
    symbols: list[str]
    tags: list[str]


def fetch_raw_news() -> list[dict]:
    # Historical stub disabled: Daily Brief must be source-bound and read from
    # SQLite `news`, never from hard-coded demo headlines.
    return []


def _tags(text: str) -> list[str]:
    t = text.lower()
    tags: list[str] = []
    if any(k in t for k in ("war", "conflict", "oil", "opec")):
        tags.extend(["macro", "energy"])
    if any(k in t for k in ("fed", "interest rate", "cpi", "inflation", "yield")):
        tags.extend(["macro", "rates"])
    if any(k in t for k in ("earnings", "guidance", "q1", "q2", "q3", "q4")):
        tags.append("earnings")
    if any(k in t for k in ("acquisition", "merger", "m&a")):
        tags.append("m&a")
    if any(k in t for k in ("regulator", "regulation", "sec", "probe", "investigation")):
        tags.append("regulation")
    return sorted(set(tags))


def _symbols(text: str) -> list[str]:
    # simple uppercase ticker heuristic
    cands = re.findall(r"\b[A-Z]{2,5}\b", text)
    return sorted(set([c for c in cands if c not in {"OPEC", "CPI", "FED"}]))[:6]


def enrich_news_with_tags(raw_news_list: list[dict]) -> list[NewsItem]:
    out: list[NewsItem] = []
    for r in raw_news_list:
        title = str(r.get("title") or "")
        summary = str(r.get("summary") or "")
        text = f"{title} {summary}"
        out.append(
            NewsItem(
                ts=str(r.get("ts") or ""),
                title=title,
                summary=summary,
                symbols=_symbols(text),
                tags=_tags(text),
            )
        )
    return out
