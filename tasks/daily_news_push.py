from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from db.client import default_db_path
from push.notify import send_alert


COMPANY_ALIASES = {
    "AVGO": ["AVGO", "Broadcom"],
    "GOOGL": ["GOOGL", "GOOG", "Alphabet", "Google"],
    "IREN": ["IREN", "Iris Energy"],
    "META": ["META", "Meta", "Facebook"],
    "MSFT": ["MSFT", "Microsoft"],
    "NVDA": ["NVDA", "Nvidia", "NVIDIA"],
    "PANW": ["PANW", "Palo Alto Networks"],
    "QQQ": ["QQQ"],
    "TSLA": ["TSLA", "Tesla"],
    "VOO": ["VOO"],
    "V": ["Visa"],
    "MA": ["Mastercard"],
    "COST": ["Costco"],
    "CEG": ["Constellation Energy"],
    "VST": ["Vistra"],
    "SNDK": ["SanDisk", "Sandisk", "SNDK"],
}

MACRO_KEYWORDS = (
    "fed",
    "federal reserve",
    "rate",
    "rates",
    "inflation",
    "cpi",
    "ppi",
    "jobs report",
    "nonfarm",
    "unemployment",
    "payrolls",
    "treasury",
    "treasury yield",
    "bond yield",
    "oil",
    "brent",
    "wti",
    "opec",
    "war",
    "middle east",
    "tariff",
    "export control",
    "vix",
    "s&p 500",
    "nasdaq",
)

MACRO_EXCLUDE_PHRASES = (
    "stocks of the week",
    "time to sell",
    "is the stock a buy",
    "which stock",
    "better stock",
)

HIGH_VALUE_HOLDING_KEYWORDS = (
    "jumped",
    "jumps",
    "surged",
    "plunged",
    "falls",
    "lawsuit",
    "settles",
    "oppose",
    "opposes",
    "earnings",
    "guidance",
    "invest",
    "investment",
    "deal",
    "contract",
    "regulator",
    "investigation",
    "movers",
)

SECTOR_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "chip",
    "semiconductor",
    "data center",
    "datacenter",
    "electricity",
    "power demand",
    "cloud",
    "cybersecurity",
    "ev",
    "oil",
    "energy demand",
)

EARNINGS_ASSERTION_RE = re.compile(
    r"\b(beats?|beat|miss(?:es|ed)?|raises?|raised|cuts?|cut|guidance|results?|reported|reports?)\b",
    re.I,
)


@dataclass
class EvidenceItem:
    text: str
    relation: str
    confidence: str
    source_title: str
    source_url: str
    published_at: str
    source_name: str
    matched_symbols: list[str]
    reason: str
    priority: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "relation": self.relation,
            "confidence": self.confidence,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "source_name": self.source_name,
            "matched_symbols": self.matched_symbols,
            "reason": self.reason,
            "priority": self.priority,
        }


def _load_recent_news(db_path: str | Path, *, hours: int = 30, limit: int = 500) -> list[dict[str, Any]]:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, source, title, COALESCE(summary,'') AS summary, COALESCE(url,'') AS url,
                   published_at, COALESCE(primary_ticker,'') AS primary_ticker,
                   COALESCE(affected_tickers,'[]') AS affected_tickers,
                   COALESCE(category,'') AS category, COALESCE(severity,'') AS severity,
                   COALESCE(one_line_zh,'') AS one_line_zh
            FROM news
            WHERE published_at >= ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _contains_alias(text: str, symbol: str) -> bool:
    aliases = COMPANY_ALIASES.get(symbol.upper(), [symbol.upper()])
    for alias in aliases:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags=re.I):
            return True
    return False


def _matched_symbols(row: dict[str, Any], symbols: set[str]) -> list[str]:
    text = f"{row.get('title') or ''} {row.get('summary') or ''}"
    return sorted([s for s in symbols if _contains_alias(text, s)])


def _keyword_hit(row: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    text = f"{row.get('title') or ''} {row.get('summary') or ''}".lower()
    for k in keywords:
        kk = k.lower()
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(kk)}(?![A-Za-z0-9])", text):
            return True
    return False


def _source_score(row: dict[str, Any]) -> float:
    src = str(row.get("source") or "").lower()
    title = str(row.get("title") or "").lower()
    score = 0.0
    if "reuters" in src:
        score += 5
    elif "associated press" in src or "barrons" in src:
        score += 3
    elif "mt newswires" in src or "investing.com" in src:
        score += 2
    elif "motley fool" in src or "24/7 wall" in src:
        score -= 2
    if any(p in title for p in ("is the stock a buy", "time to sell", "better stock", "stocks of the week")):
        score -= 3
    return score


def _is_generic_article(row: dict[str, Any]) -> bool:
    text = f"{row.get('title') or ''} {row.get('summary') or ''}".lower()
    patterns = (
        r"\bis .{1,80} a buy\b",
        r"\bshould you buy\b",
        r"\bbetter .{1,80} stock\b",
        r"\btop .{1,60} stocks\b",
        r"\bstocks of the week\b",
        r"\btime to sell\b",
        r"\bwant .{1,60} stock before\b",
    )
    return any(re.search(p, text, flags=re.I) for p in patterns)


def _priority(row: dict[str, Any], *, relation: str, matched: list[str]) -> float:
    title = str(row.get("title") or "").lower()
    score = _source_score(row)
    if relation == "holding_direct":
        score += 6
        if any(s in {"IREN", "NVDA", "AVGO", "MSFT"} for s in matched):
            score += 2
        if any(k in title for k in HIGH_VALUE_HOLDING_KEYWORDS):
            score += 3
    elif relation == "macro":
        score += 3
        if any(k in title for k in ("s&p 500", "nasdaq", "oil", "brent", "fed", "jobs", "yield", "vix")):
            score += 3
        if any(p in title for p in MACRO_EXCLUDE_PHRASES):
            score -= 8
    elif relation == "holding_sector":
        score += 1
    elif relation == "watchlist":
        score += 2
    return score


def _source_line(row: dict[str, Any], *, prefix: str = "") -> str:
    title = str(row.get("title") or "").strip()
    src = str(row.get("source") or "unknown").strip()
    ts = str(row.get("published_at") or "")[:16].replace("T", " ")
    label = f"{prefix}{title}".strip()
    return f"{label} ({src}, {ts})"


def _is_unsafe_earnings_rewrite(text: str, row: dict[str, Any]) -> bool:
    """Guard against unsupported 'beats earnings' style claims.

    Daily Brief currently only uses source titles, but keep this guard so future
    summarizers cannot turn previews/old reports into published results.
    """

    if not EARNINGS_ASSERTION_RE.search(text):
        return False
    source_text = f"{row.get('title') or ''} {row.get('summary') or ''}"
    return not EARNINGS_ASSERTION_RE.search(source_text)


def _evidence_from_row(
    row: dict[str, Any],
    *,
    relation: str,
    confidence: str,
    matched: list[str],
    reason: str,
    prefix: str = "",
) -> EvidenceItem | None:
    text = _source_line(row, prefix=prefix)
    if _is_unsafe_earnings_rewrite(text, row):
        return None
    return EvidenceItem(
        text=text,
        relation=relation,
        confidence=confidence,
        source_title=str(row.get("title") or ""),
        source_url=str(row.get("url") or ""),
        published_at=str(row.get("published_at") or ""),
        source_name=str(row.get("source") or ""),
        matched_symbols=matched,
        reason=reason,
        priority=_priority(row, relation=relation, matched=matched),
    )


def _dedupe(items: list[EvidenceItem], limit: int) -> list[EvidenceItem]:
    seen: set[str] = set()
    out: list[EvidenceItem] = []
    for item in sorted(items, key=lambda x: (x.priority, x.published_at), reverse=True):
        title_key = re.sub(r"\s+", " ", item.source_title.strip().lower())
        key = title_key or (item.source_url or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def generate_daily_news_digest(
    holdings_symbols: list[str],
    config: dict | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict:
    _ = config
    db = db_path or default_db_path()
    holdings = {s.upper() for s in holdings_symbols}
    watchlist = {s.upper() for s in load_opportunity_tickers()} - holdings
    rows = _load_recent_news(db)

    macro: list[EvidenceItem] = []
    holding_direct: list[EvidenceItem] = []
    holding_sector: list[EvidenceItem] = []
    watchlist_items: list[EvidenceItem] = []

    for row in rows:
        direct = _matched_symbols(row, holdings)
        watch = _matched_symbols(row, watchlist)
        generic = _is_generic_article(row)
        if direct:
            if generic and not _keyword_hit(row, HIGH_VALUE_HOLDING_KEYWORDS):
                continue
            ev = _evidence_from_row(
                row,
                relation="holding_direct",
                confidence="high",
                matched=direct,
                reason="标题/摘要明确提到持仓 ticker 或公司名",
            )
            if ev:
                holding_direct.append(ev)
            continue

        if _keyword_hit(row, MACRO_KEYWORDS):
            if generic:
                continue
            ev = _evidence_from_row(
                row,
                relation="macro",
                confidence="high",
                matched=[],
                reason="宏观关键词命中，且保留原始来源标题",
            )
            if ev:
                macro.append(ev)
            continue

        if watch:
            if generic:
                continue
            ev = _evidence_from_row(
                row,
                relation="watchlist",
                confidence="high",
                matched=watch,
                reason="标题/摘要明确提到观察池 ticker 或公司名",
            )
            if ev:
                watchlist_items.append(ev)
            continue

        if _keyword_hit(row, SECTOR_KEYWORDS):
            if generic:
                continue
            ev = _evidence_from_row(
                row,
                relation="holding_sector",
                confidence="medium",
                matched=[],
                reason="行业主题相关，但没有直接提到你的持仓",
            )
            if ev:
                holding_sector.append(ev)

    direct_top = _dedupe(holding_direct, 5)
    macro_top = _dedupe([m for m in macro if m.priority >= 2], 3)
    watch_top = _dedupe(watchlist_items, 3)
    sector_top = _dedupe([h for h in holding_sector if h.priority >= 0], 3)
    important_pool = [*direct_top, *macro_top]
    important_top = _dedupe([x for x in important_pool if x.priority >= 7], 2)
    if not important_top:
        important_top = _dedupe(important_pool, 2)
    important_keys = {re.sub(r"\s+", " ", x.source_title.strip().lower()) for x in important_top}
    direct_top = [x for x in direct_top if re.sub(r"\s+", " ", x.source_title.strip().lower()) not in important_keys]
    macro_top = [x for x in macro_top if re.sub(r"\s+", " ", x.source_title.strip().lower()) not in important_keys]
    opportunities = _dedupe([*watch_top, *sector_top], 2)

    digest = {
        "important_alerts": [x.as_dict() for x in important_top],
        "macro_news": [x.as_dict() for x in macro_top[:2]],
        "holding_direct": [x.as_dict() for x in direct_top[:3]],
        "holding_sector": [x.as_dict() for x in sector_top[:2]],
        "watchlist": [x.as_dict() for x in watch_top[:2]],
        "opportunities": [x.as_dict() for x in opportunities],
        # Backward-compatible keys for existing UI.
        "holdings_news": [x.as_dict() for x in direct_top[:3]],
        "source_policy": "source_bound_only",
        "news_rows_scanned": len(rows),
    }
    return digest


def _format_item(item: dict[str, Any]) -> str:
    title = item.get("text") or item.get("source_title") or ""
    url = item.get("source_url") or ""
    conf = item.get("confidence") or "unknown"
    if url:
        return f"- [{conf}] {title}\n  source: {url}"
    return f"- [{conf}] {title}"


def push_daily_news_digest_via_ntfy(digest: dict, config: dict | None = None) -> int:
    _ = config
    body = ["AlphaWatch Daily Market Radar（source-bound）", "", "重要提醒："]
    body.extend([_format_item(x) for x in digest.get("important_alerts") or []] or ["- 无"])
    body.append("")
    body.append("我的持仓：")
    body.extend([_format_item(x) for x in digest.get("holding_direct") or []] or ["- 无"])
    body.append("")
    body.append("大方向：")
    body.extend([_format_item(x) for x in digest.get("macro_news") or []] or ["- 无"])
    body.append("")
    body.append("不要错过：")
    body.extend([_format_item(x) for x in digest.get("opportunities") or []] or ["- 无"])
    body.append("")
    body.append(f"扫描新闻：{digest.get('news_rows_scanned', 0)} 条；只推 source-bound 5-8 条。")
    ok = send_alert("routine", "AlphaWatch Daily News", "\n".join(body), tags="newspaper")
    return 1 if ok else 0


def run_daily_news_push_once(config: dict | None = None) -> dict:
    holdings = load_watchlist_tickers()
    digest = generate_daily_news_digest(holdings, config=config)
    pushed = push_daily_news_digest_via_ntfy(digest, config=config)
    return {"holdings": len(holdings), "pushed": pushed, "digest": digest}
