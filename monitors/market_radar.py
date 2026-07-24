from __future__ import annotations

"""Cross-universe major move radar.

This is the layer that catches "I do not own it, but I care about this theme"
events. It scans holdings + opportunity pools + bucket/theme/guru/pinned symbols
and writes only meaningful major/urgent moves into the existing alerts table.
"""

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from data_layer.market_data import fetch_quotes
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from data_layer.guru_copybook import build_guru_symbol_meta, guru_evidence_label
from db.client import get_conn, repo_root
from monitors.auto_watch_config import auto_watch_enabled, scope_enabled, type_enabled
from monitors.quote_format import (
    format_radar_price_line,
    local_quote_currency as _local_quote_currency,
    quote_currency_for_symbol as _quote_currency_for_symbol,
)

NY = ZoneInfo("America/New_York")
ROOT = repo_root()


@dataclass
class RadarSymbol:
    symbol: str
    name: str
    source_tags: list[str] = field(default_factory=list)
    bucket: str = "其它"
    themes: list[str] = field(default_factory=list)
    is_held: bool = False
    is_watchlist: bool = False
    is_opportunity: bool = False
    is_theme_candidate: bool = False
    is_high_vol: bool = False
    is_revaluation_candidate: bool = False
    is_guru_pick: bool = False
    is_theme_signal: bool = False
    direct_buy_allowed: bool = True
    tradable_status: str = "tradable_candidate"
    aliases: list[str] = field(default_factory=list)
    primary_exchange: str = ""
    data_source_status: str = "unknown"
    fallback_symbols: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    discovered_by_theme_expansion: bool = False
    expansion_theme: str = ""
    scan_reason: str = "static_universe"
    guru_sources: list[str] = field(default_factory=list)
    quality_score: int = 5
    normal_volatility_bucket: str = "growth_stock"


@dataclass
class RadarMove:
    symbol: str
    name: str
    price: float
    day_change_pct: float
    day_change_abs_usd: float
    source_tags: list[str]
    bucket: str
    themes: list[str]
    triggered_rules: list[str]
    severity: str
    possible_reason: str
    action: str
    news_evidence: list[dict[str, Any]]
    volume_ratio: float | None = None
    intraday_range_pct: float | None = None
    abnormal_range_ratio: float | None = None
    distance_to_52w_high_pct: float | None = None
    is_theme_signal: bool = False
    direct_buy_allowed: bool = True
    tradable_status: str = "tradable_candidate"
    data_source_status: str = "ok"
    alternatives: list[str] = field(default_factory=list)
    discovered_by_theme_expansion: bool = False
    expansion_theme: str = ""
    scan_reason: str = "static_universe"
    quote_ccy: str = "USD"


def _now_et() -> datetime:
    return datetime.now(NY)


def _load_yaml(name: str) -> dict[str, Any]:
    p = ROOT / "config" / name
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _add_symbol(pool: dict[str, RadarSymbol], sym: str, *, tag: str, name: str | None = None) -> RadarSymbol:
    s = sym.upper().strip()
    if not s:
        raise ValueError("empty symbol")
    row = pool.get(s)
    if not row:
        row = RadarSymbol(symbol=s, name=name or s)
        pool[s] = row
    if tag not in row.source_tags:
        row.source_tags.append(tag)
    if name and row.name == s:
        row.name = name
    return row


def _bucket_symbols() -> tuple[dict[str, str], dict[str, list[str]]]:
    cfg = _load_yaml("opportunity_buckets.yaml")
    sym_to_bucket: dict[str, str] = {}
    sym_to_themes: dict[str, list[str]] = {}
    for bucket, body in (cfg.get("buckets") or {}).items():
        desc = str((body or {}).get("description") or "")
        for sym in (body or {}).get("symbols") or []:
            s = str(sym).upper()
            sym_to_bucket[s] = str(bucket)
            sym_to_themes.setdefault(s, []).append(str(bucket))
            if "AI" in desc or "数据中心" in desc:
                sym_to_themes[s].append("AI data center")
            if "电力" in desc or "能源" in desc:
                sym_to_themes[s].append("power demand")
    return sym_to_bucket, sym_to_themes


def _opportunity_theme_map() -> dict[str, list[str]]:
    raw = _load_yaml("opportunity_universe.yaml")
    out: dict[str, list[str]] = {}
    for row in raw.get("tickers") or []:
        if isinstance(row, str):
            sym, theme = row.upper(), ""
        else:
            sym = str((row or {}).get("symbol") or "").upper()
            theme = str((row or {}).get("theme") or "")
        if sym:
            out.setdefault(sym, [])
            if theme:
                out[sym].append(theme)
    return out


def _theme_pool() -> tuple[dict[str, list[str]], dict[str, str], dict[str, dict[str, Any]]]:
    raw = _load_yaml("theme_pool.yaml")
    sym_to_themes: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}
    symbol_meta: dict[str, dict[str, Any]] = {}
    aliases_cfg = raw.get("aliases") if isinstance(raw.get("aliases"), dict) else {}
    for theme, body in raw.items():
        if theme == "aliases":
            continue
        if not isinstance(body, dict):
            continue
        theme_name = str(theme)
        descriptions[theme_name] = str(body.get("description") or "")
        tradable = [str(x).upper().strip() for x in (body.get("tradable_candidates") or body.get("symbols") or [])]
        signal = [str(x).upper().strip() for x in (body.get("theme_signal_symbols") or [])]
        alternatives = [str(x).upper().strip() for x in (body.get("alternatives") or [])]
        for sym in [*tradable, *signal]:
            s = str(sym).upper().strip()
            if not s:
                continue
            sym_to_themes.setdefault(s, []).append(theme_name)
            desc = descriptions[theme_name]
            if desc:
                sym_to_themes[s].append(desc)
            meta = symbol_meta.setdefault(s, {})
            if s in signal:
                meta["is_theme_signal"] = True
                meta["direct_buy_allowed"] = False
                meta["tradable_status"] = meta.get("tradable_status") or "theme_signal_only_or_otc"
                meta["alternatives"] = list(dict.fromkeys([*(meta.get("alternatives") or []), *alternatives]))
            elif s in tradable:
                meta.setdefault("direct_buy_allowed", True)
                meta.setdefault("tradable_status", "tradable_candidate")
        for sym, body2 in aliases_cfg.items():
            s = str(sym).upper().strip()
            if not s or not isinstance(body2, dict):
                continue
            meta = symbol_meta.setdefault(s, {})
            meta.update(body2)
            meta["aliases"] = list(dict.fromkeys([str(x) for x in (body2.get("aliases") or [])]))
            meta["fallback_symbols"] = [str(x).upper().strip() for x in (body2.get("fallback_symbols") or [])]
    return sym_to_themes, descriptions, symbol_meta


def _load_guru_symbols() -> dict[str, list[str]]:
    """Best-effort guru/situational-awareness watch pool."""

    meta = build_guru_symbol_meta()
    out: dict[str, list[str]] = {}
    for sym, gm in meta.items():
        label = guru_evidence_label(gm, limit=2) or "guru_pool"
        out.setdefault(sym, []).append(label)
    return out


def _load_pinned_symbols() -> list[str]:
    raw = _load_yaml("settings.yaml")
    rows = raw.get("pinned_symbols") or raw.get("radar_pinned_symbols") or []
    return [str(x).upper().strip() for x in rows if str(x).strip()]


def _theme_expansion_map() -> dict[str, dict[str, Any]]:
    raw = _load_yaml("theme_expansion_map.yaml")
    return {str(k): dict(v or {}) for k, v in raw.items() if isinstance(v, dict)}


def build_radar_universe() -> dict[str, RadarSymbol]:
    pool: dict[str, RadarSymbol] = {}
    metadata = {str(k).upper(): dict(v or {}) for k, v in _load_yaml("symbol_metadata.yaml").items()}
    sym_to_bucket, sym_to_themes = _bucket_symbols()
    opp_themes = _opportunity_theme_map()
    theme_pool, _theme_desc, theme_symbol_meta = _theme_pool()
    guru = _load_guru_symbols()

    holdings = set(load_watchlist_tickers())
    if scope_enabled("holdings") or scope_enabled("watchlist"):
        for sym in holdings:
            _add_symbol(pool, sym, tag="holding")
    if scope_enabled("opportunity_pool"):
        for sym in load_opportunity_tickers():
            _add_symbol(pool, sym, tag="opportunity")
    if scope_enabled("etf_pool") or scope_enabled("theme_pool"):
        for sym in sym_to_bucket:
            if sym in {"VOO", "VTI", "SPY", "IVV", "QQQ", "QQQM"} and not scope_enabled("etf_pool"):
                continue
            _add_symbol(pool, sym, tag="theme")
    if scope_enabled("theme_pool"):
        for sym in theme_pool:
            _add_symbol(pool, sym, tag="theme")
    for sym in metadata:
        meta_type = str(metadata[sym].get("type") or "")
        if "重估" in meta_type or "高波动" in meta_type:
            if scope_enabled("revaluation_pool"):
                _add_symbol(pool, sym, tag="revaluation")
    if scope_enabled("guru_pool"):
        for sym, sources in guru.items():
            row = _add_symbol(pool, sym, tag="guru")
            row.guru_sources = list(dict.fromkeys([*row.guru_sources, *sources]))
    for sym in _load_pinned_symbols():
        _add_symbol(pool, sym, tag="pinned")

    for sym, row in pool.items():
        meta = {**theme_symbol_meta.get(sym, {}), **metadata.get(sym, {})}
        row.name = str(meta.get("name") or row.name or sym)
        row.bucket = str(meta.get("bucket") or sym_to_bucket.get(sym) or row.bucket)
        themes = [
            *sym_to_themes.get(sym, []),
            *opp_themes.get(sym, []),
            *theme_pool.get(sym, []),
            str(meta.get("theme") or ""),
            str(meta.get("revaluation_note") or ""),
        ]
        row.themes = [x for x in dict.fromkeys(t.strip() for t in themes if t.strip())]
        row.is_held = sym in holdings
        row.is_watchlist = "holding" in row.source_tags or "pinned" in row.source_tags
        row.is_opportunity = "opportunity" in row.source_tags
        row.is_theme_candidate = "theme" in row.source_tags
        row.is_theme_signal = bool(meta.get("is_theme_signal") or meta.get("tradable_status") in {"theme_signal_only", "theme_signal_only_or_otc"})
        row.direct_buy_allowed = bool(meta.get("direct_buy_allowed", not row.is_theme_signal))
        row.tradable_status = str(meta.get("tradable_status") or ("theme_signal_only_or_otc" if row.is_theme_signal else "tradable_candidate"))
        row.aliases = [str(x) for x in (meta.get("aliases") or [])]
        row.primary_exchange = str(meta.get("primary_exchange") or "")
        row.fallback_symbols = [str(x).upper().strip() for x in (meta.get("fallback_symbols") or []) if str(x).strip()]
        row.alternatives = [str(x).upper().strip() for x in (meta.get("alternatives") or []) if str(x).strip()]
        row.is_guru_pick = "guru" in row.source_tags
        row.is_revaluation_candidate = "revaluation" in row.source_tags or "重估" in str(meta.get("type") or "")
        row.is_high_vol = row.bucket == "高波动观察" or "高波动" in str(meta.get("type") or "")
        if sym in {"VOO", "VTI", "SPY", "IVV", "QQQ", "QQQM"}:
            row.normal_volatility_bucket = "ETF"
            row.quality_score = 10
        elif row.is_theme_signal:
            row.normal_volatility_bucket = "theme_signal"
            row.quality_score = 0
            row.data_source_status = "delayed_or_unstable" if row.tradable_status.endswith("otc") else "ok"
        elif row.is_high_vol:
            row.normal_volatility_bucket = "high_vol"
            row.quality_score = 6
        elif row.is_revaluation_candidate:
            row.normal_volatility_bucket = "revaluation_candidate"
            row.quality_score = 7
        elif row.is_guru_pick:
            row.normal_volatility_bucket = "guru_pick"
            row.quality_score = 7
        elif row.bucket in {"消费防御", "医疗健康", "金融 / 支付", "工业 / 基建 / 电力"}:
            row.normal_volatility_bucket = "large_cap_quality"
            row.quality_score = 8
        else:
            row.normal_volatility_bucket = "growth_stock"
            row.quality_score = 7
    return pool


@lru_cache(maxsize=256)
def _history_stats(sym: str) -> dict[str, float] | None:
    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return None
    try:
        import yfinance as yf  # noqa: PLC0415

        h = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=True)
        if h is None or h.empty or len(h.index) < 25:
            return None
        close = h["Close"].astype(float)
        high = h["High"].astype(float)
        low = h["Low"].astype(float)
        vol = h["Volume"].astype(float) if "Volume" in h else None
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else float(close.iloc[-1])
        day_high = float(high.iloc[-1])
        day_low = float(low.iloc[-1])
        intraday = (day_high - day_low) / prev_close * 100.0 if prev_close else 0.0
        ranges = ((high - low) / close.shift(1) * 100.0).dropna().tail(20)
        avg_range = float(ranges.mean()) if len(ranges) else 0.0
        avg_vol = float(vol.tail(20).mean()) if vol is not None and len(vol) else 0.0
        today_vol = float(vol.iloc[-1]) if vol is not None and len(vol) else 0.0
        high_52w = float(high.max())
        return {
            "previous_close": prev_close,
            "day_high": day_high,
            "day_low": day_low,
            "intraday_range_pct": intraday,
            "avg_intraday_range_20d": avg_range,
            "abnormal_range_ratio": (intraday / avg_range) if avg_range > 0 else 0.0,
            "volume": today_vol,
            "avg_volume_20d": avg_vol,
            "volume_ratio": (today_vol / avg_vol) if avg_vol > 0 else 0.0,
            "high_52w": high_52w,
        }
    except Exception:
        return None


def _threshold(symbol: RadarSymbol) -> float:
    return {
        "ETF": 3.0,
        "large_cap_quality": 4.0,
        "growth_stock": 5.0,
        "high_vol": 8.0,
        "revaluation_candidate": 8.0,
        "guru_pick": 6.0,
        "theme_signal": 5.0,
    }.get(symbol.normal_volatility_bucket, 5.0)


def _alias_patterns(meta: RadarSymbol) -> list[str]:
    return [meta.symbol, *meta.aliases, meta.name, *meta.fallback_symbols]


def _theme_keywords(meta: RadarSymbol) -> set[str]:
    blob = " ".join([*meta.themes, meta.bucket]).lower()
    keywords: set[str] = set()
    if any(x in blob for x in ("memory", "hbm", "dram", "nand", "storage", "存储")):
        keywords.update({"memory", "hbm", "dram", "nand", "storage", "samsung", "hynix", "micron", "sandisk"})
    if any(x in blob for x in ("semiconductor", "chip", "半导体", "芯片")):
        keywords.update({"semiconductor", "chip", "chips", "foundry", "wafer"})
    if any(x in blob for x in ("data center", "datacenter", "ai infrastructure", "数据中心")):
        keywords.update({"data center", "datacenter", "ai infrastructure", "gpu", "server"})
    if any(x in blob for x in ("power", "electric", "电力", "grid")):
        keywords.update({"power", "electricity", "grid", "utility", "utilities", "energy demand"})
    if any(x in blob for x in ("nuclear", "核电")):
        keywords.update({"nuclear", "reactor", "smr"})
    return keywords


def _expansion_text(move: RadarMove | RadarSymbol) -> str:
    if isinstance(move, RadarMove):
        parts = [move.symbol, move.name, move.bucket, *move.themes, *move.source_tags]
    else:
        parts = [move.symbol, move.name, move.bucket, *move.themes, *move.source_tags, *move.aliases]
    return " ".join(str(x) for x in parts if str(x).strip()).lower()


def _matches_expansion(move: RadarMove, cfg: dict[str, Any]) -> bool:
    text = _expansion_text(move)
    reps = {str(x).upper().strip() for x in (cfg.get("representative_symbols") or [])}
    if move.symbol.upper() in reps:
        return True
    for keyword in cfg.get("trigger_keywords") or []:
        k = str(keyword).lower().strip()
        if k and k in text:
            return True
    return False


def _expanded_symbol_meta(sym: str, theme: str, cfg: dict[str, Any], base: RadarSymbol | None = None) -> RadarSymbol:
    s = sym.upper().strip()
    row = base or RadarSymbol(symbol=s, name=s)
    if "dynamic_theme" not in row.source_tags:
        row.source_tags.append("dynamic_theme")
    if theme not in row.themes:
        row.themes.insert(0, theme)
    for k in cfg.get("trigger_keywords") or []:
        kk = str(k).strip()
        if kk and kk not in row.themes:
            row.themes.append(kk)
    row.discovered_by_theme_expansion = True
    row.expansion_theme = theme
    row.scan_reason = "theme_breakout"
    row.is_theme_candidate = True
    row.alternatives = list(dict.fromkeys([*row.alternatives, *[str(x).upper().strip() for x in (cfg.get("tradable_alternatives") or []) if str(x).strip()]]))
    reps = {str(x).upper().strip() for x in (cfg.get("representative_symbols") or [])}
    tradable = {str(x).upper().strip() for x in (cfg.get("tradable_alternatives") or [])}
    if s not in tradable and (s in reps or "." in s or s.endswith("F")):
        row.is_theme_signal = True
        row.direct_buy_allowed = False
        row.tradable_status = row.tradable_status if row.tradable_status != "tradable_candidate" else "theme_signal_only_or_foreign_or_otc"
        row.normal_volatility_bucket = "theme_signal"
        row.quality_score = 0
    return row


def _has_alias(text: str, aliases: list[str]) -> bool:
    for raw in aliases:
        alias = str(raw or "").strip()
        if not alias:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags=re.I):
            return True
    return False


def _news_evidence(conn: sqlite3.Connection, meta: RadarSymbol, limit: int = 3) -> list[dict[str, Any]]:
    rows = []
    try:
        raw_rows = conn.execute(
            """
            SELECT id, title, COALESCE(one_line_zh,'') AS one_line_zh,
                   COALESCE(source,'') AS source, COALESCE(url,'') AS url,
                   published_at, COALESCE(category,'') AS category,
                   COALESCE(affected_tickers,'[]') AS affected_tickers
            FROM news
            WHERE published_at >= datetime('now','-1 day')
            ORDER BY published_at DESC
            LIMIT 500
            """
        ).fetchall()
    except Exception:
        return []
    aliases = _alias_patterns(meta)
    keywords = _theme_keywords(meta)
    for r in raw_rows:
        text = f"{r['title']} {r['one_line_zh']} {r['category']}".lower()
        affected = []
        try:
            data = json.loads(str(r["affected_tickers"] or "[]"))
            if isinstance(data, list):
                affected = [str(x).upper() for x in data]
        except Exception:
            affected = []
        direct = meta.symbol.upper() in affected or _has_alias(text, aliases)
        thematic = bool(keywords and sum(1 for k in keywords if k in text) >= 1)
        if thematic and not direct:
            # Avoid assigning broad "AI" or unrelated mega-cap news as the
            # reason for a specific mover. Theme evidence must contain a
            # concrete industry word, not just a generic market article.
            generic_bad = {"microsoft", "meta", "apple", "amazon", "google"}
            if meta.symbol not in {"MSFT", "META", "AAPL", "AMZN", "GOOGL", "GOOG"} and any(x in text for x in generic_bad) and not any(k in text for k in {"memory", "hbm", "dram", "nand", "semiconductor", "chip", "data center", "power", "electricity"}):
                thematic = False
        if not direct and not thematic:
            continue
        rows.append(
            {
                "id": r["id"],
                "title": r["title"],
                "one_line_zh": r["one_line_zh"],
                "source": r["source"],
                "url": r["url"],
                "published_at": r["published_at"],
                "category": r["category"] or "unknown",
                "match_type": "direct" if direct else "theme",
            }
        )
        if len(rows) >= limit:
            break
    rows.sort(key=lambda x: 0 if x.get("match_type") == "direct" else 1)
    return rows[:limit]


def _possible_reason(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "暂未找到明确新闻原因，可能是主题资金异动。"
    direct = [e for e in evidence if e.get("match_type") == "direct"]
    top = (direct or evidence)[0]
    return str(top.get("one_line_zh") or top.get("title") or "有相关新闻催化。")


def _quote_for_symbol(sym: str, meta: RadarSymbol, quote_map: dict[str, Any]) -> tuple[Any | None, str]:
    q = quote_map.get(sym)
    if q and getattr(q, "px", 0):
        return q, "ok"
    for fb in meta.fallback_symbols:
        q2 = quote_map.get(fb)
        if q2 and getattr(q2, "px", 0):
            return q2, f"fallback:{fb}"
    if meta.fallback_symbols:
        try:
            fetched = {q.symbol.upper(): q for q in fetch_quotes(meta.fallback_symbols, ttl_seconds=60)}
            for fb in meta.fallback_symbols:
                q2 = fetched.get(fb)
                if q2 and getattr(q2, "px", 0):
                    return q2, f"fallback:{fb}"
        except Exception:
            pass
    return None, "missing_quote"


def _action_for_move(move_pct: float, symbol: RadarSymbol, evidence: list[dict[str, Any]]) -> str:
    if symbol.is_theme_signal or not symbol.direct_buy_allowed:
        alts = " / ".join(symbol.alternatives[:5]) or "MU / SNDK / WDC / STX / EWY"
        return f"主题信号，不直接追买；检查是否为 OTC/海外报价异常，并看替代关注：{alts}。"
    if abs(move_pct) >= 8 or symbol.is_high_vol or symbol.is_revaluation_candidate:
        return "不要追；先看新闻原因，设置回踩到推荐区间/从高点回撤 8% 提醒。"
    if evidence:
        return "可关注，但先确认新闻是否实质影响基本面。"
    return "先观察成交量和后续新闻，不追高。"


def scan_market_radar(conn: sqlite3.Connection | None = None, *, max_moves: int = 20) -> list[RadarMove]:
    owns = conn is None
    c = conn or get_conn()
    try:
        universe = build_radar_universe()
        quote_symbols = set(universe)
        for meta in universe.values():
            quote_symbols.update(meta.fallback_symbols)
        quotes = {q.symbol.upper(): q for q in fetch_quotes(sorted(quote_symbols))}
        moves: list[RadarMove] = []
        def evaluate_meta(sym: str, meta: RadarSymbol, quote_map: dict[str, Any]) -> RadarMove | None:
            q, quote_status = _quote_for_symbol(sym, meta, quote_map)
            if not q or not q.px:
                meta.data_source_status = quote_status
                return None
            meta.data_source_status = quote_status if quote_status != "ok" else meta.data_source_status if meta.is_theme_signal else "ok"
            px = float(q.px)
            chg_pct = float(q.chg_pct or 0.0)
            prev = px / (1 + chg_pct / 100.0) if chg_pct != -100 else px
            day_abs = px - prev
            local_ccy = _local_quote_currency(sym)
            # Full 1y history is expensive for a 70+ symbol radar. Most major
            # alerts already need a price move first, so only fetch detailed
            # high/low/volume stats when the quote is already worth a closer
            # look, unless explicitly overridden for debugging.
            should_load_history = abs(chg_pct) >= 3 or os.environ.get("ALPHA_RADAR_FULL_HISTORY") == "1"
            h = (_history_stats(sym) if should_load_history else None) or {}
            rules: list[str] = []
            th = _threshold(meta)
            if abs(chg_pct) >= th:
                rules.append("large_daily_move")
            if (meta.is_guru_pick or meta.is_revaluation_candidate or meta.is_theme_candidate or meta.is_theme_signal) and abs(chg_pct) >= th:
                rules.append("radar_pool_move")
            if (meta.is_high_vol or meta.is_revaluation_candidate) and abs(chg_pct) >= 8:
                rules.append("major_watchlist_move")
            if abs(chg_pct) >= 12:
                rules.append("urgent_large_move")
            # Point-change thresholds are calibrated for USD names; KRW/JPY etc. would misfire.
            if not local_ccy:
                if abs(day_abs) >= 50:
                    rules.append("large_abs_move")
                if abs(day_abs) >= 100:
                    rules.append("major_abs_move")
            intraday = h.get("intraday_range_pct")
            if intraday is not None and intraday >= th:
                rules.append("large_intraday_range")
            abnormal = h.get("abnormal_range_ratio")
            if abnormal is not None and abnormal >= 1.8:
                rules.append("abnormal_vs_self")
            volume_ratio = h.get("volume_ratio")
            if abs(chg_pct) >= 5 and volume_ratio is not None and volume_ratio >= 1.8:
                rules.append("price_volume_breakout")
            high_52w = h.get("high_52w")
            distance = None
            if high_52w:
                distance = (px / float(high_52w) - 1) * 100.0
                if px >= float(high_52w) * 0.98 and chg_pct >= 5:
                    rules.append("breakout_near_high")
            if not rules:
                return None
            if abs(chg_pct) >= 12:
                severity = "urgent"
            elif abs(chg_pct) >= 8 or ((meta.is_guru_pick or meta.is_revaluation_candidate or meta.is_theme_signal or "theme" in meta.source_tags) and abs(chg_pct) >= 8):
                severity = "major"
            elif (not local_ccy) and abs(day_abs) >= 100:
                severity = "major"
            elif abs(chg_pct) >= 5 or abs(chg_pct) >= th:
                severity = "attention"
            else:
                severity = "routine"
            evidence = _news_evidence(c, meta)
            return RadarMove(
                symbol=sym,
                name=meta.name,
                price=round(px, 2),
                day_change_pct=round(chg_pct, 2),
                day_change_abs_usd=round(day_abs, 2),
                source_tags=meta.source_tags,
                bucket=meta.bucket,
                themes=meta.themes[:5],
                triggered_rules=list(dict.fromkeys(rules)),
                severity=severity,
                possible_reason=_possible_reason(evidence),
                action=_action_for_move(chg_pct, meta, evidence),
                news_evidence=evidence,
                volume_ratio=round(float(volume_ratio), 2) if volume_ratio is not None else None,
                intraday_range_pct=round(float(intraday), 2) if intraday is not None else None,
                abnormal_range_ratio=round(float(abnormal), 2) if abnormal is not None else None,
                distance_to_52w_high_pct=round(float(distance), 2) if distance is not None else None,
                is_theme_signal=meta.is_theme_signal,
                direct_buy_allowed=meta.direct_buy_allowed,
                tradable_status=meta.tradable_status,
                data_source_status=meta.data_source_status,
                alternatives=meta.alternatives,
                discovered_by_theme_expansion=meta.discovered_by_theme_expansion,
                expansion_theme=meta.expansion_theme,
                scan_reason=meta.scan_reason,
                quote_ccy=_quote_currency_for_symbol(sym),
            )

        for sym, meta in universe.items():
            move = evaluate_meta(sym, meta, quotes)
            if move:
                moves.append(move)

        # Theme breakout: add a synthetic rule to members when >=3 names in same theme jump 5%+.
        by_theme: dict[str, list[str]] = {}
        for m in moves:
            if m.day_change_pct < 5:
                continue
            for t in m.themes:
                key = "AI data center" if "data" in t.lower() or "ai" in t.lower() else "power demand" if "power" in t.lower() or "电力" in t else t
                by_theme.setdefault(key, []).append(m.symbol)
        breakout_themes = {k: v for k, v in by_theme.items() if len(set(v)) >= 3}
        if breakout_themes:
            for m in moves:
                for theme, syms in breakout_themes.items():
                    if m.symbol in syms and "theme_breakout" not in m.triggered_rules:
                        m.triggered_rules.append("theme_breakout")
                        m.possible_reason = f"{theme} 同主题多只股票同步走强；" + m.possible_reason
                        if m.severity == "attention":
                            m.severity = "major"

        # Dynamic Theme Expansion:
        # When the known universe already shows a synchronized theme move,
        # temporarily scan that theme's core representatives even if the user
        # never hand-added each ticker. This is what catches Samsung/SK Hynix
        # as AI memory signals after MU/SNDK/semis start moving together.
        expansion_map = _theme_expansion_map()
        triggered_expansions: dict[str, dict[str, Any]] = {}
        for expansion_theme, cfg in expansion_map.items():
            matching = [m for m in moves if abs(m.day_change_pct) >= 5 and _matches_expansion(m, cfg)]
            if len({m.symbol for m in matching}) >= 3:
                triggered_expansions[expansion_theme] = cfg
        if triggered_expansions:
            representative_symbols = {
                str(sym).upper().strip()
                for cfg in triggered_expansions.values()
                for sym in (cfg.get("representative_symbols") or [])
                if str(sym).strip()
            }
            missing_quotes = sorted(s for s in representative_symbols if s not in quotes)
            if missing_quotes:
                try:
                    quotes.update({q.symbol.upper(): q for q in fetch_quotes(missing_quotes, ttl_seconds=60)})
                except Exception:
                    pass
            existing_moves = {m.symbol: m for m in moves}
            for expansion_theme, cfg in triggered_expansions.items():
                for sym0 in cfg.get("representative_symbols") or []:
                    sym = str(sym0).upper().strip()
                    if not sym:
                        continue
                    meta = _expanded_symbol_meta(sym, expansion_theme, cfg, universe.get(sym))
                    universe[sym] = meta
                    if sym in existing_moves:
                        m = existing_moves[sym]
                        m.discovered_by_theme_expansion = True
                        m.expansion_theme = expansion_theme
                        m.scan_reason = "theme_breakout"
                        m.is_theme_signal = meta.is_theme_signal
                        m.direct_buy_allowed = meta.direct_buy_allowed
                        m.tradable_status = meta.tradable_status
                        m.alternatives = list(dict.fromkeys([*m.alternatives, *meta.alternatives]))
                        if "dynamic_theme" not in m.source_tags:
                            m.source_tags.append("dynamic_theme")
                        if "theme_expansion" not in m.triggered_rules:
                            m.triggered_rules.append("theme_expansion")
                        if m.is_theme_signal:
                            m.possible_reason = f"{expansion_theme} 主题扩展发现；" + m.possible_reason
                        continue
                    move = evaluate_meta(sym, meta, quotes)
                    if move:
                        if "theme_expansion" not in move.triggered_rules:
                            move.triggered_rules.append("theme_expansion")
                        if move.severity == "attention" and (move.is_theme_signal or abs(move.day_change_pct) >= 8):
                            move.severity = "major"
                        move.possible_reason = f"{expansion_theme} 主题扩展发现；" + move.possible_reason
                        moves.append(move)
                        existing_moves[sym] = move
        moves.sort(key=lambda m: ({"urgent": 3, "major": 2, "attention": 1}.get(m.severity, 0), abs(m.day_change_pct), abs(m.day_change_abs_usd)), reverse=True)
        return moves[:max_moves]
    finally:
        if owns:
            c.close()


def emit_market_radar_alerts(conn: sqlite3.Connection, *, push_levels: tuple[str, ...] = ("urgent", "major")) -> int:
    if not auto_watch_enabled() or not type_enabled("major_move"):
        return 0
    moves = scan_market_radar(conn)
    inserted = 0
    today = _now_et().date().isoformat()
    for m in moves:
        if m.severity not in push_levels:
            continue
        dedupe = f"market-radar:{m.symbol}:{','.join(m.triggered_rules)}:{today}"
        evidence_lines = []
        for e in m.news_evidence[:2]:
            title = e.get("one_line_zh") or e.get("title") or ""
            src = e.get("source") or ""
            evidence_lines.append(f"{src}: {title}")
        why = (
            f"来源：{' / '.join(m.source_tags)}；主题：{' / '.join(m.themes[:3]) or m.bucket}。"
            + ("\n可能原因：\n- " + "\n- ".join(evidence_lines) if evidence_lines else "\n暂未找到明确新闻原因，可能是主题资金异动。")
        )
        relation = "这是主题信号票，不一定适合直接买入；重点看同主题可交易替代标的。" if m.is_theme_signal else "即使你没持有，只要它属于主题池/重估池/大佬作业池，发生重大异动也值得知道。"
        risk = (
            "OTC/海外报价可能延迟或流动性差。它负责提醒主题在动，不负责提示买入；不要追高。"
            if m.is_theme_signal
            else "重大异动不是买入信号。涨太快时优先设回踩提醒，别追。"
        )
        cur = conn.execute(
            """INSERT OR IGNORE INTO alerts
               (level, category, symbol, title, what_happened, why_matters, relation_to_you,
                watch_next, risk, dedupe_key, occurred_at, pushed_ntfy)
               VALUES (?, 'market_radar', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                m.severity,
                m.symbol,
                f"AlphaWatch: {m.symbol} 重大异动 {m.day_change_pct:+.1f}%",
                format_radar_price_line(
                    symbol=m.symbol,
                    price=m.price,
                    day_change_pct=m.day_change_pct,
                    day_change_abs=m.day_change_abs_usd,
                )[2]
                + f" 触发：{', '.join(m.triggered_rules)}。",
                why,
                relation,
                m.action,
                risk,
                dedupe,
                _now_et().isoformat(timespec="seconds"),
            ),
        )
        inserted += int(cur.rowcount > 0)
    return inserted


def _resolve_radar_symbol(query: str, universe: dict[str, RadarSymbol]) -> str:
    q = str(query or "").strip()
    if not q:
        return ""
    direct = q.upper()
    if direct in universe:
        return direct

    q_lower = q.lower()
    for sym, row in universe.items():
        names = [row.name, *row.aliases, *row.fallback_symbols]
        if any(q_lower == str(name).lower() for name in names if str(name).strip()):
            return sym
    for sym, row in universe.items():
        names = [row.name, *row.aliases]
        if any(q_lower in str(name).lower() for name in names if str(name).strip()):
            return sym
    return direct


def radar_debug(symbol: str) -> dict[str, Any]:
    universe = build_radar_universe()
    sym = _resolve_radar_symbol(symbol, universe)
    row = universe.get(sym)
    out: dict[str, Any] = {
        "query": symbol,
        "resolved_symbol": sym,
        "symbol": sym,
        "in_radar_universe": bool(row),
        "in_static_universe": bool(row),
    }
    if row:
        out["radar_symbol"] = asdict(row)
        out["discovered_by_theme_expansion"] = row.discovered_by_theme_expansion
        out["expansion_theme"] = row.expansion_theme
        out["scan_reason"] = row.scan_reason
    conn = get_conn(read_only=True)
    try:
        moves = {m.symbol: m for m in scan_market_radar(conn, max_moves=500)}
        alert = conn.execute(
            """
            SELECT id, level, category, title, occurred_at, pushed_ntfy, dedupe_key
            FROM alerts
            WHERE symbol = ? AND category = 'market_radar'
            ORDER BY occurred_at DESC, id DESC
            LIMIT 1
            """,
            (sym,),
        ).fetchone()
    finally:
        conn.close()
    move = moves.get(sym)
    out["triggered"] = bool(move)
    if move:
        out["move"] = asdict(move)
        out["discovered_by_theme_expansion"] = move.discovered_by_theme_expansion
        out["expansion_theme"] = move.expansion_theme
        out["scan_reason"] = move.scan_reason
        out["quote_source_used"] = move.data_source_status
        out["latest_quote_timestamp"] = "latest_market_data_cache_or_provider"
    else:
        quote_symbols = [sym]
        if row:
            quote_symbols.extend(row.fallback_symbols)
        qmap = {q.symbol.upper(): q for q in fetch_quotes(quote_symbols)}
        q, quote_status = _quote_for_symbol(sym, row, qmap) if row else (qmap.get(sym), "ok" if qmap.get(sym) else "missing_quote")
        if q:
            out["latest_quote"] = {
                "price": q.px,
                "day_change_pct": q.chg_pct,
                "quote_status": quote_status,
                "quote_source_used": quote_status,
                "latest_quote_timestamp": "latest_market_data_cache_or_provider",
            }
        if not q:
            out["reason_not_triggered"] = "行情源没有返回有效报价；如果是 OTC/海外票，需要检查 fallback ticker 或手动缓存。"
        else:
            out["reason_not_triggered"] = "未达到重大异动阈值；主题信号票仍在 radar_universe 中等待触发。"
    out["alert_written"] = bool(alert)
    out["ntfy_sent"] = bool(alert and int(alert["pushed_ntfy"] or 0) == 1)
    if alert:
        out["latest_alert"] = dict(alert)
    elif move and move.severity in {"urgent", "major"}:
        out["reason_not_sent"] = "本次扫描触发 major/urgent，但还没有运行 emit_market_radar_alerts 或写入已被 dedupe 拦截。"
    elif move:
        out["reason_not_sent"] = "仅为 watch/attention 级别：页面显示，不默认 ntfy。"
    else:
        out["reason_not_sent"] = out.get("reason_not_triggered")
    return out


def universe_stats() -> dict[str, Any]:
    u = build_radar_universe()
    counts: dict[str, int] = {}
    for row in u.values():
        for tag in row.source_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return {"total": len(u), "by_source": counts}
