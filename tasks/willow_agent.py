"""Willow Stock Agent — 大脑(规则优先,$0,可离线)。

产出**一份**今日建议 JSON,同时喂给首页 UI 和(后续)ntfy 推送:
- 奶奶版一句话总结
- 今日动作(做什么/不做什么/盯什么),只讲最需要注意的前几只
- 每只持仓的单一结论(动作 + 因果 + 下一步)
- 市场温度(复用 market_regime)
- 组合集中度提醒

设计原则(见 design_notes/brain):规则优先、绝不下单、按资产类别阈值、
新闻 source-bound。LLM 仅在配置了 key 时用于润色,不在此模块强依赖。
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from data_layer.universe import load_watchlist_tickers
from tasks.holding_commentary import symbol_indicator_snapshot, verdict_for_snapshot

SGT = ZoneInfo("Asia/Shanghai")
_REPO = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)

# 英文动作 -> (中文, 关注度基分, 是否"需要注意")
ACTION_META: dict[str, tuple[str, int, bool]] = {
    "Buy small": ("小额买", 70, True),
    "Add on weakness": ("逢跌加", 62, True),
    "Slower DCA": ("放慢定投", 45, True),
    "Normal DCA": ("正常定投", 25, False),
    "Hold": ("持有不动", 18, False),
    "Wait for confirmation": ("等待确认", 55, True),
    "Do not chase": ("别追高", 60, True),
    "Avoid for now": ("暂时避开", 75, True),
}

# 用于集中度判断:算作"科技/AI 半导体"敞口的角色与 ticker
_TECH_TICKERS = {
    "QQQ", "SMH", "SOXX", "MSFT", "GOOGL", "GOOG", "META", "NVDA", "AVGO",
    "AAPL", "AMZN", "TSM", "PANW", "MU", "AMD", "IREN", "CRWV", "SMR", "BE",
}


def _action_zh(action: str) -> str:
    return ACTION_META.get(action, (action, 30, False))[0]


def _action_priority(action: str) -> int:
    return ACTION_META.get(action, (action, 30, False))[1]


def _action_needs_attention(action: str) -> bool:
    return ACTION_META.get(action, (action, 30, False))[2]


def _load_cash(load_warnings: list[str]) -> float:
    try:
        from data_layer.watchlist_resolver import watchlist_path  # noqa: PLC0415

        raw = yaml.safe_load(watchlist_path().read_text(encoding="utf-8")) or {}
        for k in ("account_cash_usd", "cash_usd", "cash"):
            if raw.get(k) is not None:
                return max(0.0, float(raw[k]))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load cash from watchlist")
        load_warnings.append(f"cash unavailable: {exc}")
    return 0.0


def _positions(load_warnings: list[str]) -> dict[str, dict[str, float]]:
    """{ticker: {qty, avg}} from watchlist.yaml。"""
    out: dict[str, dict[str, float]] = {}
    try:
        from data_layer.portfolio_analytics import load_positions  # noqa: PLC0415
        from data_layer.watchlist_resolver import watchlist_path  # noqa: PLC0415

        for p in load_positions(watchlist_path()):
            out[p.ticker.upper()] = {"qty": float(p.qty), "avg": float(p.avg_cost_per_share)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load positions")
        load_warnings.append(f"positions unavailable: {exc}")
    return out


def _fallback_quotes(symbols: list[str], load_warnings: list[str]) -> dict[str, dict[str, float]]:
    """当日线快照缺失时,用 fetch_quotes 兜底 px/chg(离线则 demo)。"""
    out: dict[str, dict[str, float]] = {}
    try:
        from data_layer.market_data import fetch_quotes  # noqa: PLC0415

        for q in fetch_quotes(symbols) or []:
            out[str(q.symbol).upper()] = {"px": float(q.px), "chg_pct": float(q.chg_pct)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load fallback quotes")
        load_warnings.append(f"fallback quotes unavailable: {exc}")
    return out


def _market_temp() -> dict[str, str]:
    try:
        from signals.market_regime import compute_market_regime  # noqa: PLC0415

        r = compute_market_regime()
        return {"label": getattr(r, "label", "caution"), "zh": getattr(r, "zh", "谨慎"),
                "note": getattr(r, "note", "")}
    except Exception:
        return {"label": "unknown", "zh": "未知", "note": "大盘数据暂不可用"}


def _news_digest(holdings: list[str]) -> dict[str, list[dict[str, Any]]]:
    try:
        from tasks.daily_news_push import generate_daily_news_digest  # noqa: PLC0415

        d = generate_daily_news_digest(holdings)
        return {
            "important": d.get("important_alerts") or [],
            "holding": d.get("holding_direct") or [],
            "macro": d.get("macro_news") or [],
            "opportunities": d.get("opportunities") or [],
        }
    except Exception:
        return {"important": [], "holding": [], "macro": [], "opportunities": []}


def _headline(regime_label: str, stocks: list[dict[str, Any]], flags: list[str]) -> tuple[str, str, str]:
    """返回 (奶奶版一句话, 组合动作, 风险级别)。"""
    do_not = [s for s in stocks if s["action"] in ("Do not chase", "Avoid for now")]
    add = [s for s in stocks if s["action"] in ("Buy small", "Add on weakness")]

    if regime_label == "risk_off":
        return (
            "今天大盘偏弱(SPY/QQQ 跌破 200 日线),不适合追涨;有现金也先按兵不动、"
            "只做已计划好的核心定投。",
            "WAIT",
            "attention",
        )

    hot_names = "、".join(s["ticker"] for s in do_not[:3])
    if do_not and len(do_not) >= max(2, len(stocks) // 3):
        base = f"今天别急着追高({hot_names} 偏热);核心 ETF 可按计划正常定投,新钱分批别一次买满。"
        risk = "attention"
        pact = "HOLD"
    elif add and not do_not:
        base = "今天有几只回落到值得关注的位置,可以小额分批;但别把子弹一次打光。"
        risk = "routine"
        pact = "ADD_SMALL"
    else:
        base = "今天没有特别的公司大事,主要跟随大盘和利率;核心仓正常定投,其它持有观察即可。"
        risk = "routine"
        pact = "NORMAL_DCA"

    if "科技偏重" in flags:
        base += " 提醒:你的组合已偏重科技/AI,别再无意识加同一种风险。"
    return base, pact, risk


def build_advice() -> dict[str, Any]:
    """产出今日建议 JSON。纯函数、无副作用、可离线。"""

    load_warnings: list[str] = []
    try:
        holdings = load_watchlist_tickers()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load watchlist tickers")
        load_warnings.append(f"watchlist load failed: {exc}")
        holdings = []
    pos = _positions(load_warnings)
    cash = _load_cash(load_warnings)
    fallback = _fallback_quotes(holdings, load_warnings)

    stocks: list[dict[str, Any]] = []
    total_mv = 0.0
    priced: dict[str, float] = {}
    degraded = 0

    for sym in holdings:
        snap = symbol_indicator_snapshot(sym)
        if snap:
            is_degraded = False
            px = float(snap.get("px") or 0.0)
            chg = float(snap.get("chg_pct") or 0.0)
            verdict = verdict_for_snapshot(snap)
            action = verdict["action"]
            reason = verdict["reason"]
            watch = verdict["watch_next"]
            role = snap.get("role", "other")
            rsi_v = snap.get("rsi")
            pos_52 = snap.get("pos_52")
        else:
            is_degraded = True
            degraded += 1
            fq = fallback.get(sym.upper(), {})
            px = float(fq.get("px") or 0.0)
            chg = float(fq.get("chg_pct") or 0.0)
            action, reason, watch = "Hold", "行情/指标暂不可用,先持有观察。", "等数据恢复后再判断"
            role, rsi_v, pos_52 = "other", None, None

        qty = pos.get(sym.upper(), {}).get("qty", 0.0)
        avg = pos.get(sym.upper(), {}).get("avg", 0.0)
        mv = qty * px if px else qty * avg
        total_mv += mv
        priced[sym.upper()] = px or avg
        pnl_pct = ((px / avg - 1.0) * 100.0) if (px and avg) else None

        stocks.append({
            "ticker": sym.upper(),
            "role": role,
            "qty": qty,
            "px": px,
            "chg_pct": chg,
            "pnl_pct": pnl_pct,
            "rsi": rsi_v,
            "pos_52": pos_52,
            "action": action,
            "action_zh": _action_zh(action),
            "reason_zh": reason,
            "watch_next_zh": watch,
            "mv": mv,
            "needs_attention": _action_needs_attention(action),
            "priority": _action_priority(action) + min(30, abs(chg) * 4),
            "degraded": is_degraded,
        })

    # 仓位权重 + 集中度
    tech_w = 0.0
    top_w = 0.0
    top_name = ""
    for s in stocks:
        w = (s["mv"] / total_mv * 100.0) if total_mv > 0 else 0.0
        s["weight_pct"] = w
        if s["ticker"] in _TECH_TICKERS:
            tech_w += w
        if w > top_w:
            top_w, top_name = w, s["ticker"]

    flags: list[str] = []
    try:
        from tasks.willow_memory import get_rules  # noqa: PLC0415

        rules = get_rules()
    except Exception:
        rules = {"max_tech_pct": 55.0, "max_single_name_pct": 25.0}
    max_tech = float(rules.get("max_tech_pct", 55.0))
    max_single = float(rules.get("max_single_name_pct", 25.0))
    if tech_w >= max_tech:
        flags.append("科技偏重")
    if top_w >= max_single:
        flags.append(f"单票偏重({top_name} 约 {top_w:.0f}%)")

    temp = _market_temp()

    # 免费新闻(无需 key):地缘/宏观/芯片/大盘/个股 + 今日大事因果链
    try:
        from data_layer.free_news import get_news  # noqa: PLC0415

        news = get_news(holdings)
    except Exception:
        news = {"geopolitics": [], "macro": [], "chips": [], "market": [],
                "holdings": [], "big_events": []}
    big_events = news.get("big_events") or []

    headline, pact, risk = _headline(temp["label"], stocks, flags)
    if big_events:
        short = big_events[0]["headline"]
        if len(short) > 42:
            short = short[:42] + "…"
        headline = f"⚠️ 今日大事:{short}。" + headline
        if risk == "routine":
            risk = "attention"

    stocks.sort(key=lambda s: s["priority"], reverse=True)
    attention = [s for s in stocks if s["needs_attention"]][:5]

    # 今日动作分组
    def _names(actions: set[str]) -> list[str]:
        return [f"{s['ticker']}({s['action_zh']})" for s in stocks if s["action"] in actions]

    today_actions = {
        "可以做": _names({"Buy small", "Add on weakness", "Normal DCA"}),
        "先别做": _names({"Do not chase", "Avoid for now", "Wait for confirmation"}),
        "盯着看": [f"{s['ticker']}:{s['watch_next_zh']}" for s in attention],
    }

    try:
        from tasks.willow_memory import diff_calls  # noqa: PLC0415

        changes = diff_calls(stocks)
    except Exception:
        changes = []

    # Agentic multi-factor focus ranking (offline-safe; gurus omitted here to
    # avoid a network fetch in the core path — the UI can pass them in).
    try:
        from tasks import conviction  # noqa: PLC0415

        focus = conviction.build_focus(stocks, gurus=None, news=news, top_n=5)
    except Exception:
        focus = []

    return {
        "as_of": datetime.now(SGT).strftime("%Y-%m-%d %H:%M"),
        "observed_at": None,
        "as_of_label": datetime.now(SGT).strftime("%m/%d %H:%M SGT"),
        "headline_zh": headline,
        "portfolio": {
            "action": pact,
            "risk_level": risk,
            "total_mv": total_mv,
            "cash": cash,
            "total_asset": total_mv + cash,
            "tech_weight_pct": tech_w,
            "concentration_flags": flags,
        },
        "market_temp": temp,
        "today_actions": today_actions,
        "changes": changes,
        "big_events": big_events,
        "stocks": stocks,
        "attention": attention,
        "focus": focus,
        "news": news,
        "data_degraded": degraded,
        "source": "rule_based",
        "load_warnings": load_warnings,
    }


# ---------------------------------------------------------------------------
# 阶段 2:Ask Agent 问答 + 每日推送
# ---------------------------------------------------------------------------

_BUY_WORDS = ("买", "加", "补", "入", "抄底", "定投", "建仓")
_SELL_WORDS = ("卖", "减", "清", "跑", "止盈", "止损", "出")
_ALARM_WORDS = ("提醒", "alarm", "到价", "报警")


def _intent(question: str) -> str:
    q = (question or "").lower()
    if any(w in q for w in _ALARM_WORDS):
        return "alarm"
    if any(w in q for w in _SELL_WORDS):
        return "sell"
    if any(w in q for w in _BUY_WORDS):
        return "buy"
    return "general"


def _stock_from_advice(ticker: str, advice: dict[str, Any] | None) -> dict[str, Any] | None:
    if not advice:
        return None
    for s in advice.get("stocks", []):
        if s["ticker"] == ticker:
            return s
    return None


def _if_buy_text(action: str) -> str:
    if action in ("Do not chase",):
        return "别追高。真想买就等回踩到 MA50 附近、且没有坏消息时,只买很小一笔试仓。"
    if action in ("Wait for confirmation",):
        return "先等企稳信号(重新站上 MA50/MA200)再考虑,别急着接刀。"
    if action in ("Avoid for now",):
        return "现在先别买。高波动 + 跌破 200 日线,等基本面/现金流看清楚再说。"
    if action in ("Add on weakness",):
        return "可以小额分批:第一笔只放计划金额的 20%–30%,进一步回落再加,别一次买满。"
    if action in ("Buy small",):
        return "可以小额试仓,但严格控制单只仓位,留子弹给更大的回调。"
    if action in ("Normal DCA",):
        return "这是核心仓,正常按周定投即可,不用择时精确抄底。"
    return "没有明显买点,持有观察即可。"


def answer_question(ticker: str, question: str, advice: dict[str, Any] | None = None,
                    *, use_llm: bool = False) -> dict[str, Any]:
    """规则化回答"现在能买吗/跌了要加吗/设提醒"之类问题。"""

    tk = str(ticker or "").upper().strip()
    intent = _intent(question)

    stock = _stock_from_advice(tk, advice)
    if stock is None:
        snap = symbol_indicator_snapshot(tk)
        if not snap:
            return {
                "ticker": tk,
                "granny": f"{tk} 现在取不到行情数据,先不下结论。",
                "action_zh": "等待数据",
                "reasons": ["行情/指标暂不可用(可能离线或被限流)。"],
                "if_buy": "等数据恢复后再判断。",
                "note": "",
                "source": "rule_based",
            }
        v = verdict_for_snapshot(snap)
        stock = {
            "ticker": tk, "action": v["action"], "action_zh": _action_zh(v["action"]),
            "reason_zh": v["reason"], "watch_next_zh": v["watch_next"],
            "chg_pct": snap.get("chg_pct"), "rsi": snap.get("rsi"),
            "pos_52": snap.get("pos_52"), "px": snap.get("px"),
            "role": snap.get("role", "other"), "weight_pct": None, "pnl_pct": None,
        }

    action = stock["action"]
    facts: list[str] = []
    if stock.get("chg_pct") is not None:
        facts.append(f"今日 {stock['chg_pct']:+.1f}%")
    if stock.get("rsi") is not None:
        r = stock["rsi"]
        tag = "偏热" if r >= 70 else ("偏冷/超卖" if r <= 30 else "中性")
        facts.append(f"RSI {r:.0f}({tag})")
    if stock.get("pos_52") is not None:
        facts.append(f"52周位置 {stock['pos_52']:.0f}%")
    if stock.get("weight_pct"):
        facts.append(f"占你组合约 {stock['weight_pct']:.0f}%")
    if stock.get("pnl_pct") is not None:
        facts.append(f"你的持仓浮盈亏 {stock['pnl_pct']:+.1f}%")

    if intent == "alarm":
        px = stock.get("px") or 0.0
        granny = f"给 {tk} 设个价格提醒挺好,到价我推你手机。"
        if_buy = (f"建议:跌破提醒设在现价下方(如 ${px*0.92:,.2f} 附近做加仓水坑),"
                  f"突破提醒设在现价上方。到页面「价格提醒」里加即可。") if px else "到页面「价格提醒」里设置目标价。"
    elif intent == "sell":
        granny = f"{tk} 目前的纪律结论是「{stock['action_zh']}」,不是无脑卖出信号。"
        if_buy = "除非基本面变坏或你要控制仓位,否则不建议因为单日波动就卖。"
    else:
        granny = f"{tk} 现在的结论:{stock['action_zh']}。"
        if_buy = _if_buy_text(action)

    note = ""
    if stock.get("role") in ("mega", "core") and tk in _TECH_TICKERS and advice:
        if "科技偏重" in (advice.get("portfolio", {}).get("concentration_flags") or []):
            note = "注意:你的组合已偏重科技/AI,再加要想清楚是不是在堆同一种风险。"

    # Fold in the multi-factor conviction so the Q&A speaks the same language
    # as the 今日聚焦 panel.
    conviction_line = ""
    try:
        from tasks import conviction as _conv  # noqa: PLC0415

        gurus = advice.get("gurus_loaded") if advice else None
        c = _conv.score_stock(stock)
        conviction_line = f"综合信念 {c['conviction']:.0f}/100（{c['direction']}）"
        top_factors = "、".join(f["label"] for f in c.get("factors", [])[:3])
        if top_factors:
            conviction_line += f"，主要看：{top_factors}"
    except Exception:
        pass

    reasons = ([stock.get("reason_zh", "")] + facts) if stock.get("reason_zh") else list(facts)
    if conviction_line:
        reasons = [conviction_line] + reasons

    out = {
        "ticker": tk,
        "granny": granny,
        "action_zh": stock["action_zh"],
        "reasons": reasons,
        "if_buy": if_buy,
        "watch_next": stock.get("watch_next_zh", ""),
        "note": note,
        "source": "rule_based",
    }

    if use_llm:
        out = _llm_rewrite_answer(out, question)
    return out


def _llm_rewrite_answer(ans: dict[str, Any], question: str) -> dict[str, Any]:
    try:
        from llm.router import TokenGuard  # noqa: PLC0415

        facts = "；".join(ans.get("reasons") or [])
        prompt = (
            "你是面向股票新手的中文助手,回答要口语、简短、直接,不堆免责声明,"
            "保留动作标签,不要编造新闻或数字。\n"
            f"用户问:{question}\n"
            f"股票:{ans['ticker']}\n结论:{ans['action_zh']}\n依据:{facts}\n"
            f"如果想买:{ans['if_buy']}\n"
            "请用 3-4 句自然中文回答。"
        )
        r = TokenGuard().call("willow_qa", prompt)
        txt = (r or {}).get("text") or ""
        if txt.strip():
            ans = dict(ans)
            ans["llm_text"] = txt.strip()
            ans["source"] = f"llm:{r.get('routed')}"
    except Exception:
        pass
    return ans


def build_push_text(advice: dict[str, Any] | None = None) -> str:
    """把今日建议压成一条适合 ntfy 的简短中文。"""
    advice = advice or build_advice()
    p = advice.get("portfolio", {})
    ta = advice.get("today_actions", {})
    lines = [
        f"🐕 Willow 今日简报 · {advice.get('as_of_label','')}",
        "",
        advice.get("headline_zh", ""),
        "",
        f"市场:{advice.get('market_temp',{}).get('zh','')}｜总资产 ${p.get('total_asset',0):,.0f}",
    ]
    big = advice.get("big_events") or []
    if big:
        lines.append("")
        lines.append("🌍 今日大事:")
        for e in big[:2]:
            lines.append(f"• {e['headline']}")
            lines.append(f"  {e['cause_effect']}")
    can = ta.get("可以做") or []
    dont = ta.get("先别做") or []
    watch = ta.get("盯着看") or []
    if can:
        lines.append("✅ 可以做:" + "、".join(can[:4]))
    if dont:
        lines.append("⛔ 先别做:" + "、".join(dont[:4]))
    if watch:
        lines.append("👀 盯着:" + "；".join(watch[:3]))
    focus = advice.get("focus") or []
    if focus:
        top = focus[:3]
        lines.append("")
        lines.append("🎯 今日聚焦:")
        for f in top:
            lines.append(f"• {f['ticker']} {f['direction']}（信念{f['conviction']:.0f}）")
    flags = p.get("concentration_flags") or []
    if flags:
        lines.append("⚠️ " + "、".join(flags))
    lines.append("")
    lines.append("详情见本机 Willow's Stock Agent 页面。不代下单。")
    return "\n".join(lines)


def push_daily_digest(advice: dict[str, Any] | None = None, *, force: bool = False) -> bool:
    """发送今日简报到 ntfy(需 .env 配置 NTFY_TOPIC)。内容没变且冷却期内则跳过。"""
    advice = advice or build_advice()
    if not force:
        try:
            from tasks.willow_memory import should_push_digest  # noqa: PLC0415

            if not should_push_digest(advice):
                return False
        except Exception:
            pass
    body = build_push_text(advice)
    try:
        from push.notify import send_alert  # noqa: PLC0415

        ok = send_alert("routine", "Willow Daily Brief", body, tags="dog2")
    except Exception:
        ok = False
    if ok:
        try:
            from tasks.willow_memory import save_snapshot  # noqa: PLC0415

            save_snapshot(advice, trigger="digest")
        except Exception:
            pass
    return ok


if __name__ == "__main__":
    import json

    print(json.dumps(build_advice(), ensure_ascii=False, indent=2, default=str))