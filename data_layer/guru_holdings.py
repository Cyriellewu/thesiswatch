"""大佬持仓(superinvestor 13F)——从 Dataroma 抓取,带组合百分比。

来源:dataroma.com/m/holdings.php?m=CODE(静态 HTML,含「% of Portfolio」列)。
- 礼貌抓取:自定义 User-Agent,经理之间 sleep。
- 结果缓存 12 小时(13F 季度更新,不需要频繁抓)。
- 离线 / 失败时回退到缓存或空列表,绝不抛错。

注意事项(展示给用户):13F 为季度披露、约 45 天延迟,仅美股多头,不含空头/多数期权。
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from data_layer.cache import JsonTTLCache

log = logging.getLogger(__name__)

# 经理标签 -> Dataroma 代码(已在 managers 页核实)。挑选与科技/AI 组合最相关的。
DATAROMA_MANAGERS: dict[str, str] = {
    "Chase Coleman (Tiger Global)": "TGM",   # NVDA/AVGO/TSM/GOOGL/META/MSFT/AMZN
    "David Tepper (Appaloosa)": "AM",         # AMZN/MU/GOOG/UBER/TSM/NVDA
    "Duan Yongping (H&H 段永平)": "HH",       # AAPL/BRK/NVDA/PDD/TSLA
    "David Rolfe (Wedgewood)": "WP",          # GOOGL/TSM/META/AAPL/MSFT
    "Warren Buffett (Berkshire)": "BRK",      # 质量锚:AAPL/GOOGL
    "Terry Smith (Fundsmith)": "FS",          # 质量成长
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (AlphaWatch personal research; contact local)",
    "Accept-Language": "en-US,en;q=0.9",
}
_BASE = "https://www.dataroma.com/m/holdings.php?m={code}"
_CACHE_TTL = 12 * 3600


def _parse_holdings(html: str) -> dict[str, Any]:
    """从 Dataroma holdings 页 HTML 解析 {manager, period, portfolio_value, holdings[]}。"""
    from bs4 import BeautifulSoup  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    name_el = soup.select_one("#f_name")
    manager = name_el.get_text(strip=True) if name_el else ""

    period, pval = "", ""
    p2 = soup.select_one("#p2")
    if p2:
        spans = p2.find_all("span")
        if spans:
            period = spans[0].get_text(strip=True)
        for sp in spans:
            t = sp.get_text(strip=True)
            if t.startswith("$"):
                pval = t
                break

    holdings: list[dict[str, Any]] = []
    grid = soup.select_one("table#grid")
    if grid:
        body = grid.find("tbody") or grid
        for tr in body.find_all("tr"):
            link = tr.select_one("a[href*='stock.php?sym=']")
            if not link:
                continue
            m = re.search(r"sym=([A-Za-z.\-]+)", link.get("href", ""))
            sym = (m.group(1) if m else link.get_text(strip=True)).upper().strip()
            name = ""
            span = link.find("span")
            if span:
                name = span.get_text(strip=True).lstrip("- ").strip()
            cells = tr.find_all("td")
            # % 权重 = 紧跟 stock 单元格后的那个 td
            pct = None
            stock_idx = None
            for i, td in enumerate(cells):
                if td.select_one("a[href*='stock.php?sym=']"):
                    stock_idx = i
                    break
            if stock_idx is not None and stock_idx + 1 < len(cells):
                raw = cells[stock_idx + 1].get_text(strip=True).replace("%", "")
                try:
                    pct = float(raw)
                except ValueError:
                    pct = None
            if sym and pct is not None:
                holdings.append({"symbol": sym, "name": name, "pct": pct})

    holdings.sort(key=lambda x: x["pct"], reverse=True)
    return {"manager": manager, "period": period, "portfolio_value": pval, "holdings": holdings}


def fetch_manager_holdings(code: str, *, ttl: int = _CACHE_TTL, force: bool = False) -> dict[str, Any]:
    """抓单个经理持仓(带缓存)。失败返回缓存或空。"""
    cache = JsonTTLCache("guru_holdings", ttl)
    ck = cache.key(code.upper())
    if not force:
        hit = cache.get(ck)
        if hit:
            return hit

    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return cache.get(ck) or {"manager": "", "period": "", "portfolio_value": "", "holdings": []}

    try:
        import requests  # noqa: PLC0415

        resp = requests.get(_BASE.format(code=code), headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        parsed = _parse_holdings(resp.text)
        if parsed.get("holdings"):
            cache.set(ck, parsed)
        return parsed
    except Exception as exc:  # 网络/解析失败都不抛
        log.warning("dataroma fetch failed for %s: %s", code, exc)
        return cache.get(ck) or {"manager": "", "period": "", "portfolio_value": "", "holdings": []}


def load_all_gurus(*, top_n: int = 10, managers: dict[str, str] | None = None,
                   polite_delay: float = 2.0) -> list[dict[str, Any]]:
    """抓取全部经理(默认 DATAROMA_MANAGERS),每人取前 top_n 持仓。"""
    managers = managers or DATAROMA_MANAGERS
    out: list[dict[str, Any]] = []
    first = True
    for label, code in managers.items():
        if not first and os.environ.get("ALPHAWATCH_OFFLINE") != "1":
            time.sleep(polite_delay)
        first = False
        data = fetch_manager_holdings(code)
        out.append({
            "label": label,
            "code": code,
            "manager": data.get("manager") or label,
            "period": data.get("period", ""),
            "portfolio_value": data.get("portfolio_value", ""),
            "holdings": (data.get("holdings") or [])[:top_n],
        })
    return out


def overlap_with_holdings(gurus: list[dict[str, Any]], my_tickers: list[str]) -> dict[str, list[dict[str, Any]]]:
    """返回 {我的ticker: [{label, pct}, ...]},显示哪些大佬也持有我的票。"""
    mine = {t.upper() for t in my_tickers}
    result: dict[str, list[dict[str, Any]]] = {t: [] for t in mine}
    for g in gurus:
        for h in g.get("holdings", []):
            sym = str(h.get("symbol", "")).upper()
            if sym in mine:
                result[sym].append({"label": g["label"], "pct": h.get("pct")})
    return {k: sorted(v, key=lambda x: (x.get("pct") or 0), reverse=True) for k, v in result.items() if v}


def build_insights(gurus: list[dict[str, Any]], my_tickers: list[str],
                   my_weights: dict[str, float] | None = None) -> dict[str, Any]:
    """规则化分析:大佬持仓和「我的持仓」有什么关系。"""
    mine = {t.upper() for t in my_tickers}
    my_weights = {k.upper(): v for k, v in (my_weights or {}).items()}

    # 每个 ticker -> [(guru_label, pct)]
    holders: dict[str, list[tuple[str, float]]] = {}
    for g in gurus:
        for h in g.get("holdings", []):
            sym = str(h.get("symbol", "")).upper()
            holders.setdefault(sym, []).append((g["label"], float(h.get("pct") or 0.0)))

    def _avg(sym: str) -> float:
        lst = holders.get(sym, [])
        return sum(p for _, p in lst) / len(lst) if lst else 0.0

    # 1) 你的票里,被最多大佬持有的(共识背书)
    consensus = []
    for tk in sorted(mine):
        lst = holders.get(tk, [])
        if lst:
            consensus.append({"ticker": tk, "count": len(lst), "avg_pct": round(_avg(tk), 1),
                              "top": max(lst, key=lambda x: x[1])})
    consensus.sort(key=lambda x: (x["count"], x["avg_pct"]), reverse=True)

    # 2) 你持有、但没有任何大佬碰的票
    not_held = [tk for tk in sorted(mine) if tk not in holders]

    # 3) 大佬重仓、但你没有的(潜在点子)——按"持有人数×平均权重"排序
    ideas = []
    _SKIP = {"BRK.A", "BRK.B", "EWY", "MAVF"}  # 跳过控股/ETF 之类
    for sym, lst in holders.items():
        if sym in mine or sym in _SKIP:
            continue
        ideas.append({"ticker": sym, "count": len(lst), "avg_pct": round(_avg(sym), 1),
                      "example": max(lst, key=lambda x: x[1])})
    ideas.sort(key=lambda x: (x["count"], x["avg_pct"]), reverse=True)

    # 4) 和你最像的大佬(重叠只数最多)
    closest = []
    for g in gurus:
        syms = {str(h.get("symbol", "")).upper() for h in g.get("holdings", [])}
        overlap = sorted(mine & syms)
        if overlap:
            closest.append({"label": g["label"], "overlap": overlap, "count": len(overlap)})
    closest.sort(key=lambda x: x["count"], reverse=True)

    return {
        "consensus": consensus,
        "not_held": not_held,
        "ideas": ideas[:6],
        "closest": closest,
    }


def insight_lines(ins: dict[str, Any]) -> list[str]:
    """把 insights 变成给新手看的中文句子。"""
    out: list[str] = []
    cons = ins.get("consensus") or []
    if cons:
        top = cons[0]
        out.append(
            f"🏆 **最被认可**:你的 **{top['ticker']}** 有 {top['count']} 位大佬也在拿"
            f"(如 {top['top'][0].split(' (')[0]} 占其组合 {top['top'][1]:.1f}%),说明是主流共识票。"
        )
        multi = [c for c in cons if c["count"] >= 2]
        if len(multi) >= 2:
            names = "、".join(f"{c['ticker']}({c['count']}人)" for c in multi[:5])
            out.append(f"✅ **多位大佬都拿**:{names} —— 你在这些票上和主流机构站一边。")
    closest = ins.get("closest") or []
    if closest:
        c = closest[0]
        out.append(
            f"👯 **和你最像的大佬**:{c['label'].split(' (')[0]},重叠 {c['count']} 只"
            f"({'、'.join(c['overlap'][:6])})。可以重点参考他的调仓。"
        )
    ideas = ins.get("ideas") or []
    if ideas:
        names = "、".join(f"{i['ticker']}({i['count']}人/均{i['avg_pct']:.0f}%)" for i in ideas[:4])
        out.append(f"💡 **大佬重仓、你还没有**:{names}。可作为研究点子(不是买入信号,先看基本面)。")
    nh = ins.get("not_held") or []
    if nh:
        out.append(
            f"🔎 **大佬基本不碰**:{'、'.join(nh)} —— 多是 ETF 或高波动小票"
            f"(13F 不含 ETF、多数不含这类),所以看不到很正常,别据此下结论。"
        )
    return out


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    gurus = load_all_gurus(top_n=8)
    for g in gurus:
        print(f"\n=== {g['label']} ({g['period']}, {g['portfolio_value']}) ===")
        for h in g["holdings"][:8]:
            print(f"  {h['symbol']:<6} {h['pct']:>5.1f}%  {h['name']}")
    print("\nOverlap:", json.dumps(
        overlap_with_holdings(gurus, ["AAPL", "GOOGL", "AMZN", "META", "NVDA"]),
        ensure_ascii=False))
