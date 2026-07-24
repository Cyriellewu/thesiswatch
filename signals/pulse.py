"""把行情 / 头条 / 宏观转成 `market_signals`（规则层，不调用 LLM）。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from data_layer import finnhub_client
from data_layer.market_data import Quote

NY = ZoneInfo("America/New_York")


def _ny_today() -> str:
    return datetime.now(NY).date().isoformat()


def _news_keywords_score(title: str) -> tuple[str, int]:
    t = title.lower()
    urgent_kw = ("bankrupt", "sec investigation", "halt", "criminal", "lawsuit", "subpoena")
    major_kw = (
        "earnings",
        "profit warning",
        "guidance",
        "downgrade",
        "upgrade",
        "merger",
        "acquisition",
        "takeover",
        "fda",
        "ceo",
        "cfo",
        "layoff",
        "macro",
        "investigation",
    )
    for k in urgent_kw:
        if k in t:
            return "urgent", 90
    for k in major_kw:
        if k in t:
            return "major", 65
    return "attention", 35


def _stable_hint(text: str, *, prefix: str, n: int = 12) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]
    return f"{prefix}:{h}"


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def emit_price_signals(
    conn: sqlite3.Connection,
    *,
    quotes: list[Quote],
    holding_syms: set[str],
    opportunity_syms: set[str],
) -> list[int]:
    """按涨跌幅生成价格信号；返回新插入的 market_signals.id。"""

    hold_attn = _env_float("ALPHA_HOLDING_MOVE_ATTN_PCT", 4.0)
    hold_major = _env_float("ALPHA_HOLDING_MOVE_MAJOR_PCT", 6.0)
    hold_urgent = _env_float("ALPHA_HOLDING_MOVE_URGENT_PCT", 9.0)
    opp_attn = _env_float("ALPHA_OPP_MOVE_ATTN_PCT", 6.0)
    opp_major = _env_float("ALPHA_OPP_MOVE_MAJOR_PCT", 9.0)

    ny_d = _ny_today()
    ids: list[int] = []
    for q in quotes:
        sym = q.symbol.upper()
        chg = float(q.chg_pct or 0)
        bucket = ""
        if sym in holding_syms:
            bucket = "holding"
        elif sym in opportunity_syms:
            bucket = "opportunity"
        else:
            continue
        a = abs(chg)
        if bucket == "holding":
            if a < hold_attn:
                continue
            if a >= hold_urgent:
                level = "urgent"
                score = 92
            elif a >= hold_major:
                level = "major"
                score = 75
            else:
                level = "attention"
                score = 50
        else:
            if a < opp_attn:
                continue
            if a >= opp_major:
                level = "major"
                score = 72
            else:
                level = "attention"
                score = 48

        dedupe_hint = f"price:{sym}:{ny_d}"
        payload = {
            "px": float(q.px),
            "chg_pct": chg,
            "bucket": bucket,
            "implied_alert_level": level,
            "dedupe_hint": dedupe_hint,
        }
        cur = conn.execute(
            """INSERT INTO market_signals (symbol, signal_type, payload_json, score, source)
               VALUES (?,?,?,?, 'price_pulse')""",
            (sym, "daily_price_move", json.dumps(payload, ensure_ascii=False), score),
        )
        ids.append(int(cur.lastrowid))
    return ids


def emit_news_signals(
    conn: sqlite3.Connection,
    *,
    headlines: list[dict],
    holding_syms: set[str],
    per_symbol_cap: int = 2,
) -> list[int]:
    """对持仓相关的头条生成信号（已在外层限制总条数）。"""

    ids: list[int] = []
    per_sym: dict[str, int] = {}
    ny_d = _ny_today()

    for h in headlines:
        sym = (h.get("symbol") or "").upper().strip()
        title = (h.get("title") or "").strip()
        if sym not in holding_syms or not title:
            continue
        n = per_sym.get(sym, 0)
        if n >= per_symbol_cap:
            continue
        per_sym[sym] = n + 1
        lvl, score = _news_keywords_score(title)
        payload = {
            "title": title,
            "implied_alert_level": lvl,
            "dedupe_hint": _stable_hint(f"{sym}|{ny_d}|{title}", prefix=f"news:{sym}:{ny_d}"),
        }
        cur = conn.execute(
            """INSERT INTO market_signals (symbol, signal_type, payload_json, score, source)
               VALUES (?,?,?,?, 'news_pulse')""",
            (sym, "company_headline", json.dumps(payload, ensure_ascii=False), score),
        )
        ids.append(int(cur.lastrowid))
    return ids


def macro_strip_to_score(strip: dict) -> tuple[dict, int, str]:
    """返回 (macro dict with _score/_level, score, suggested_level)。"""

    try:
        vix = float(strip.get("vix") or 0)
    except (TypeError, ValueError):
        vix = 0.0
    try:
        qqq = float(strip.get("qqq_chg_pct") or 0)
    except (TypeError, ValueError):
        qqq = 0.0
    try:
        spy = float(strip.get("spy_chg_pct") or 0)
    except (TypeError, ValueError):
        spy = 0.0

    level = "routine"
    score = 25
    if vix >= 32 and (qqq <= -2.0 or spy <= -2.0):
        level = "urgent"
        score = 90
    elif vix >= 28 or qqq <= -2.5 or spy <= -2.5:
        level = "major"
        score = 70
    elif vix >= 24 or qqq <= -1.5:
        level = "attention"
        score = 45

    out = {**strip, "_score": score, "_level": level}
    out["dedupe_hint"] = f"macro:{_ny_today()}"
    return out, score, level


def emit_macro_signal(conn: sqlite3.Connection, macro_strip: dict) -> int:
    """写入一条宏观快照信号供推送与日后 agent 共用。"""

    payload, score, _lvl = macro_strip_to_score(dict(macro_strip))
    cur = conn.execute(
        """INSERT INTO market_signals (symbol, signal_type, payload_json, score, source)
           VALUES (NULL,'macro_strip',?,?, 'macro_pulse')""",
        (json.dumps(payload, ensure_ascii=False), int(score)),
    )
    return int(cur.lastrowid)


def _insert_signal(
    conn: sqlite3.Connection,
    *,
    symbol: str | None,
    signal_type: str,
    payload: dict,
    score: int,
    source: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO market_signals (symbol, signal_type, payload_json, score, source)
           VALUES (?,?,?,?,?)""",
        (symbol, signal_type, json.dumps(payload, ensure_ascii=False), int(score), source),
    )
    return int(cur.lastrowid)


def emit_technical_signals(
    conn: sqlite3.Connection,
    *,
    symbols: list[str],
) -> list[int]:
    """Phase-A: price_breakout + volume_spike (rule-based)."""
    ids: list[int] = []
    if not symbols:
        return ids

    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception:
        return ids

    ny_d = _ny_today()
    for sym in symbols:
        s = str(sym).upper().strip()
        if not s:
            continue
        try:
            hist = yf.Ticker(s).history(period="4mo", interval="1d", auto_adjust=True)
        except Exception:
            continue
        if hist is None or hist.empty or len(hist.index) < 30:
            continue
        try:
            close = hist["Close"].astype(float)
            vol = hist["Volume"].astype(float)
        except Exception:
            continue

        last_px = float(close.iloc[-1])
        prev_high = float(close.iloc[:-5].max()) if len(close) > 10 else float(close.iloc[:-1].max())
        if prev_high > 0 and last_px >= prev_high * 1.02:
            payload = {
                "title": f"突破信号：{s}",
                "what": f"{s} 收盘价站上近段区间高位（约 +2% 确认）。",
                "why": "突破常代表资金愿意在更高价格继续接力。",
                "risk": "假突破很常见，需结合后续量能与回踩表现。",
                "implied_alert_level": "attention",
                "dedupe_hint": f"breakout:{s}:{ny_d}",
            }
            ids.append(
                _insert_signal(
                    conn,
                    symbol=s,
                    signal_type="price_breakout",
                    payload=payload,
                    score=56,
                    source="tech_rule",
                )
            )

        vol_last = float(vol.iloc[-1]) if len(vol) else 0.0
        vol_avg20 = float(vol.tail(21).iloc[:-1].mean()) if len(vol) > 21 else float(vol[:-1].mean() or 0.0)
        if vol_last > 0 and vol_avg20 > 0 and vol_last >= vol_avg20 * 2.2:
            payload = {
                "title": f"放量信号：{s}",
                "what": f"{s} 当日成交量约为近 20 日均量的 {vol_last / vol_avg20:.1f} 倍。",
                "why": "放量代表参与者明显增加，走势延续概率通常更高。",
                "risk": "放量也可能是情绪尾声，需防冲高回落。",
                "implied_alert_level": "attention",
                "dedupe_hint": f"volume:{s}:{ny_d}",
            }
            ids.append(
                _insert_signal(
                    conn,
                    symbol=s,
                    signal_type="volume_spike",
                    payload=payload,
                    score=54,
                    source="tech_rule",
                )
            )
    return ids


def emit_company_dynamic_signals(
    conn: sqlite3.Connection,
    *,
    headlines: list[dict],
    holding_syms: set[str],
) -> list[int]:
    """Phase-A: earnings_beat + analyst_upgrade + insider_buy."""
    ids: list[int] = []
    ny_d = _ny_today()

    # earnings beat from headline semantics (cheap rule)
    for h in headlines:
        sym = str((h or {}).get("symbol") or "").upper().strip()
        title = str((h or {}).get("title") or "").strip()
        if not sym or sym not in holding_syms or not title:
            continue
        tl = title.lower()
        if "earnings" in tl and (
            "beat" in tl or "tops estimates" in tl or "above estimate" in tl or "raises guidance" in tl
        ):
            payload = {
                "title": f"财报超预期：{sym}",
                "what": title,
                "why": "财报与指引共振时，通常比单日价格噪声更有持续性。",
                "risk": "也可能是一次性因素，需继续观察后续指引兑现。",
                "implied_alert_level": "major",
                "dedupe_hint": _stable_hint(
                    f"{sym}|{ny_d}|{title}",
                    prefix=f"earnings_beat:{sym}:{ny_d}",
                ),
            }
            ids.append(
                _insert_signal(
                    conn,
                    symbol=sym,
                    signal_type="earnings_beat",
                    payload=payload,
                    score=68,
                    source="headline_rule",
                )
            )

    if not finnhub_client.is_configured():
        return ids

    for sym in sorted(holding_syms):
        # Analyst recommendation trend
        try:
            recs = finnhub_client.fetch_recommendation_trends(sym)
        except Exception:
            recs = []
        if len(recs) >= 2:
            cur = recs[0] or {}
            prev = recs[1] or {}
            cur_bull = int(cur.get("strongBuy") or 0) + int(cur.get("buy") or 0)
            prev_bull = int(prev.get("strongBuy") or 0) + int(prev.get("buy") or 0)
            cur_bear = int(cur.get("sell") or 0) + int(cur.get("strongSell") or 0)
            prev_bear = int(prev.get("sell") or 0) + int(prev.get("strongSell") or 0)
            if (cur_bull - prev_bull) >= 2 and cur_bear <= prev_bear:
                payload = {
                    "title": f"评级上调倾向：{sym}",
                    "what": f"近两期 recommendation 中，看多评级净增加约 {cur_bull - prev_bull}。",
                    "why": "多家机构同向上调时，常有基本面或订单边际改善。",
                    "risk": "分析师也会追涨修正，不能单独作为买入依据。",
                    "implied_alert_level": "attention",
                    "dedupe_hint": f"analyst_upgrade:{sym}:{ny_d}",
                }
                ids.append(
                    _insert_signal(
                        conn,
                        symbol=sym,
                        signal_type="analyst_upgrade",
                        payload=payload,
                        score=57,
                        source="finnhub_reco",
                    )
                )

        # Insider net buy
        try:
            rows = finnhub_client.fetch_insider_transactions(sym, days_back=30)
        except Exception:
            rows = []
        net_change = 0.0
        for r in rows[:120]:
            try:
                net_change += float(r.get("change") or 0.0)
            except (TypeError, ValueError):
                continue
        if net_change >= 10_000:
            payload = {
                "title": f"内部人净买入：{sym}",
                "what": f"近 30 天内部人净变动约 +{net_change:,.0f} 股。",
                "why": "管理层/内部人净买入通常代表对中期预期更有信心。",
                "risk": "样本可能混有计划性交易；卖出信号解读尤其容易误判。",
                "implied_alert_level": "attention",
                "dedupe_hint": f"insider_buy:{sym}:{ny_d}",
            }
            ids.append(
                _insert_signal(
                    conn,
                    symbol=sym,
                    signal_type="insider_buy",
                    payload=payload,
                    score=60,
                    source="finnhub_insider",
                )
            )
    return ids
