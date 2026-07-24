"""免费新闻(无需任何 API key)。

来源:
- Google News RSS(topic 搜索):地缘/战争、宏观/美联储、半导体/AI、大盘
- yfinance 个股新闻:持仓公司层面

产出已分类的新闻 + 「今日大事」因果链(规则化)。全部离线安全、失败不抛错、缓存 30 分钟。
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any

from data_layer.cache import JsonTTLCache

log = logging.getLogger(__name__)
_TTL = 30 * 60

# 分类 -> Google News 查询词
_TOPICS = {
    "geopolitics": "Iran Israel war OR Strait of Hormuz oil price OR Middle East conflict",
    "macro": "Federal Reserve interest rates inflation CPI jobs report",
    "chips": "semiconductor AI chip Nvidia TSMC China export memory HBM",
    "market": "US stock market today S&P 500 Nasdaq",
}

# 因果链规则:命中关键词 -> 一句「发生什么→为什么在意→影响」
_CAUSE_RULES = [
    (("hormuz", "iran", "israel", "middle east", "oil price", "crude", "opec", "war"),
     "🛢️ 地缘冲突/油价扰动 → 油价上涨会推高通胀 → 美联储更难降息、利率上行 → QQQ 等高估值科技承压;能源/国防相对受益。"),
    (("federal reserve", "fed ", "interest rate", "rate cut", "rate hike", "inflation", "cpi", "pce", "hawkish", "jobs report", "payrolls"),
     "🏦 美联储/通胀数据 → 影响降息节奏与利率 → 利率越高,成长股估值压力越大(你的 QQQ/大型科技最敏感)。"),
    (("export", "chip ban", "semiconductor", "nvidia", "hbm", "memory", "sk hynix", "tsmc", "tariff"),
     "🔧 芯片/出口限制 → 直接影响半导体需求与供给 → NVDA/AVGO/TSM/SMH 波动加大,需分清是需求变化还是获利回吐。"),
]


def _clean_title(title: str, source: str) -> str:
    t = title.strip()
    if source and t.endswith(f" - {source}"):
        t = t[: -len(f" - {source}")].strip()
    elif " - " in t:
        # 去掉结尾的 " - 来源"
        head, _, tail = t.rpartition(" - ")
        if 0 < len(tail) <= 40:
            t = head.strip()
    return t


def _gnews(query: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        import feedparser  # noqa: PLC0415

        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en")
        d = feedparser.parse(url)
        out = []
        for e in d.entries[:limit]:
            src = ""
            if e.get("source") and isinstance(e.source, dict):
                src = e.source.get("title", "")
            out.append({
                "title": _clean_title(e.get("title", ""), src),
                "url": e.get("link", ""),
                "source": src,
                "published": e.get("published", "")[:16],
            })
        return out
    except Exception as exc:
        log.warning("gnews failed for %s: %s", query, exc)
        return []


def _yf_ticker_news(symbols: list[str], per: int = 1, cap: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import yfinance as yf  # noqa: PLC0415

        for sym in symbols[:cap]:
            try:
                items = yf.Ticker(sym).news or []
            except Exception:
                continue
            for it in items[:per]:
                c = it.get("content") or it
                title = c.get("title") if isinstance(c, dict) else ""
                if not title:
                    continue
                url = ""
                if isinstance(c, dict):
                    prov = c.get("clickThroughUrl") or c.get("canonicalUrl") or {}
                    url = prov.get("url", "") if isinstance(prov, dict) else ""
                out.append({"ticker": sym.upper(), "title": title, "url": url,
                            "source": (c.get("provider", {}) or {}).get("displayName", "") if isinstance(c, dict) else ""})
    except Exception as exc:
        log.warning("yf news failed: %s", exc)
    return out


_EVERGREEN = ("what is", "how is", "why do", "relationship between", "explained",
              "history", "guide to", "everything you need", " a buy", "should you buy")


def _big_events(by_topic: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    """从地缘/宏观/芯片头条里提炼「今日大事」+ 因果链。跳过科普/常青文。"""
    events: list[dict[str, str]] = []
    seen_rules: set[str] = set()
    for cat in ("geopolitics", "macro", "chips"):
        for item in by_topic.get(cat, [])[:4]:
            title_l = item["title"].lower()
            if any(p in title_l for p in _EVERGREEN):
                continue
            for kws, effect in _CAUSE_RULES:
                if effect in seen_rules:
                    continue
                if any(k in title_l for k in kws):
                    events.append({"headline": item["title"], "source": item.get("source", ""),
                                   "url": item.get("url", ""), "cause_effect": effect})
                    seen_rules.add(effect)
                    break
        if len(events) >= 3:
            break
    return events


def get_news(holdings: list[str]) -> dict[str, Any]:
    cache = JsonTTLCache("free_news", _TTL)
    ck = cache.key(*sorted(h.upper() for h in holdings))
    hit = cache.get(ck)
    if hit:
        return hit
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return {"geopolitics": [], "macro": [], "chips": [], "market": [], "holdings": [], "big_events": []}

    by_topic = {cat: _gnews(q, limit=5) for cat, q in _TOPICS.items()}
    holdings_news = _yf_ticker_news(holdings)
    result = {
        **by_topic,
        "holdings": holdings_news,
        "big_events": _big_events(by_topic),
    }
    if any(by_topic.values()):
        cache.set(ck, result)
    return result


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    n = get_news(["NVDA", "AVGO", "TSM", "MSFT", "GOOGL", "META"])
    print("BIG EVENTS:")
    for e in n["big_events"]:
        print(" •", e["headline"], "\n   ->", e["cause_effect"])
    for cat in ("geopolitics", "macro", "chips", "market"):
        print(f"\n[{cat}]")
        for it in n[cat][:3]:
            print("  -", it["title"], "|", it["source"])
    print("\n[holdings]")
    for it in n["holdings"][:6]:
        print("  -", it["ticker"], it["title"][:70])
