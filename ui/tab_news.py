from __future__ import annotations

import importlib
import json
import re
import traceback
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data_layer import macro_data
from data_layer.earnings_data import upcoming_earnings
from data_layer.market_data import fetch_quotes
from data_layer import portfolio_analytics as pa
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from db.client import default_db_path, get_conn
from monitors.market_brake import build_market_brake_snapshot
from monitors.market_radar import scan_market_radar
from monitors.watchlist_sentinels import add_sentinel, list_sentinels
from ui.radar_widget import render_alert_debug, render_major_moves

_SEV = ["pending", "urgent", "important", "attention", "routine"]
_SEV_EMOJI = {"pending": "⏳", "urgent": "🔴", "important": "🟠", "attention": "🟡", "routine": "🟢"}
_SEV_LABEL = {"pending": "AI 分析中", "urgent": "紧急", "important": "重要", "attention": "关注", "routine": "日常"}
_STANCE_LABEL = {
    "bullish": "📈 利好",
    "bearish": "📉 利空",
    "noise": "🔇 噪音",
    "neutral": "➖ 中性",
}
_GENERIC_SOURCE_HINTS = ("motley fool", "seeking alpha", "benzinga", "zacks", "investorplace")
_GENERIC_TITLE_HINTS = ("top stocks", "best stocks", "should you buy", "why this stock")

_ALIASES = {
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
}

_RECAP_HOUR_CHOICES: tuple[int, ...] = (12, 24, 48, 72, 120, 168)


def _clamp_recap_hours(hours: int) -> int:
    h = int(hours)
    return h if h in _RECAP_HOUR_CHOICES else 48


def _digest_line(item: object) -> str:
    if isinstance(item, dict):
        title = str(item.get("text") or item.get("source_title") or "").strip()
        url = str(item.get("source_url") or "").strip()
        conf = str(item.get("confidence") or "unknown").strip()
        reason = str(item.get("reason") or "").strip()
        matched = item.get("matched_symbols") or []
        who = f" · {', '.join(map(str, matched))}" if matched else ""
        suffix = f" · {reason}" if reason else ""
        if url:
            return f"- **[{conf}]**{who} · [{title}]({url}){suffix}"
        return f"- **[{conf}]**{who} · {title}{suffix}"
    return str(item)


def _direct_holding_matches(row: object, holdings: set[str]) -> list[str]:
    try:
        title = str(row["title"] if not isinstance(row, dict) else row.get("title") or "")
        summary = str(row["summary"] if not isinstance(row, dict) else row.get("summary") or "")
    except Exception:
        title = ""
        summary = ""
    text = f"{title} {summary}"
    out: list[str] = []
    for sym in sorted(holdings):
        aliases = _ALIASES.get(sym, [sym])
        if any(re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags=re.I) for alias in aliases):
            out.append(sym)
    return out


def _parse_js(raw: str | None, fallback):
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _safe_intensity(value: object) -> int:
    if value is None:
        return 5
    if isinstance(value, (int, float)):
        try:
            if isinstance(value, float) and value != value:  # NaN
                return 5
            return max(1, min(10, int(value)))
        except (TypeError, ValueError, OverflowError):
            return 5
    raw = str(value).strip().lower()
    zh_map = {"高": 8, "较高": 7, "中高": 7, "中": 5, "中等": 5, "低": 3, "较低": 2}
    if raw in zh_map:
        return zh_map[raw]
    en_map = {"high": 8, "medium": 5, "mid": 5, "low": 3}
    if raw in en_map:
        return en_map[raw]
    try:
        return max(1, min(10, int(float(raw))))
    except (TypeError, ValueError):
        return 5


def _clean_ai_text(value: object) -> str:
    raw = str(value or "").strip()
    return "" if raw.lower() in {"null", "none", "nan", ""} else raw


def _load_fresh_news_pipeline():
    modules = [
        "news_pipeline.fetchers.base",
        "news_pipeline.fetchers.finnhub",
        "news_pipeline.fetchers.reuters",
        "news_pipeline.fetchers.sec_edgar",
        "news_pipeline.fetchers.yfinance_news",
        "news_pipeline.deduper",
        "news_pipeline.storage",
        "news_pipeline.orchestrator",
    ]
    loaded = [importlib.import_module(name) for name in modules]
    for mod in loaded:
        importlib.reload(mod)
    return loaded[-1].run_news_pipeline


def _load_fresh_news_tagger():
    mod = importlib.import_module("news_pipeline.news_tagger")
    mod = importlib.reload(mod)
    return mod.run_news_tagging


def _load_fresh_storyline_module():
    # Reload storage first because storyline_generator imports schema helpers from it.
    storage = importlib.import_module("news_pipeline.storage")
    importlib.reload(storage)
    mod = importlib.import_module("news_pipeline.storyline_generator")
    return importlib.reload(mod)


def _load_fresh_daily_news_push():
    mod = importlib.import_module("tasks.daily_news_push")
    mod = importlib.reload(mod)
    return mod.run_daily_news_push_once


def _today_action_items() -> dict:
    positions = pa.load_positions()
    symbols = [p.ticker for p in positions]
    quotes = fetch_quotes(symbols)
    demo_like = 0
    for idx, q in enumerate(quotes[: min(5, len(quotes))]):
        if abs(float(q.px) - round(100 + idx * 3.71, 2)) < 0.01:
            demo_like += 1
    quote_is_demo_fallback = bool(quotes) and demo_like >= min(3, len(quotes))
    qmap = {q.symbol.upper(): q for q in quotes}
    load_cash = getattr(pa, "load_account_cash_usd", None)
    cash = float(load_cash()) if callable(load_cash) else 0.0
    warnings: list[str] = []
    infos: list[str] = []
    if not positions:
        return {"status": "暂无持仓数据。", "warnings": warnings, "infos": infos, "cash": cash}

    for p in positions:
        if quote_is_demo_fallback:
            break
        q = qmap.get(p.ticker.upper())
        if not q or p.avg_cost_per_share <= 0:
            continue
        ratio = float(q.px) / p.avg_cost_per_share if p.avg_cost_per_share > 0 else 1.0
        if ratio < 0.4 or ratio > 2.2:
            # yfinance 离线时 fetch_quotes 会回 deterministic demo rows；不要用假价触发行动提醒。
            continue
        pnl_pct = (float(q.px) - p.avg_cost_per_share) / p.avg_cost_per_share
        if pnl_pct >= 0.30:
            warnings.append(f"💰 {p.ticker} 已 {pnl_pct:+.0%}，考虑设止盈/回撤提醒。")
        elif pnl_pct <= -0.15:
            warnings.append(f"⚠️ {p.ticker} 已 {pnl_pct:+.0%}，接近止损警戒，先确认基本面有没有变。")

    try:
        from data_layer.finnhub_client import is_configured as finnhub_is_configured

        today = date.today()
        events = (
            [e for e in upcoming_earnings(symbols) if 0 <= (e.report_date - today).days <= 7]
            if finnhub_is_configured()
            else []
        )
    except Exception:
        events = []
    if events:
        bits = [f"{e.report_date.strftime('%m/%d')} {e.symbol}" for e in events[:5]]
        infos.append("⏰ 本周财报：" + " · ".join(bits))

    if cash >= 500:
        infos.append(f"💰 现金约 ${cash:,.0f}。按计划优先看 JNJ/UNH/VST/COST 这类补非科技仓的机会。")

    status = "✅ 持仓状态稳定，今日无需操作。" if not warnings else "今天主要是提醒/设价位，不需要冲动交易。"
    return {"status": status, "warnings": warnings[:4], "infos": infos[:3], "cash": cash}


def _render_today_actions() -> None:
    st.markdown("### 📋 今日提醒")
    data = _today_action_items()
    with st.container(border=True):
        if data["warnings"]:
            st.warning(str(data["status"]))
            for line in data["warnings"]:
                st.write(line)
        else:
            st.success(str(data["status"]))
        for line in data["infos"]:
            st.info(line)
        try:
            moves = [m for m in scan_market_radar(max_moves=12) if m.severity in {"urgent", "major"}]
        except Exception:
            moves = []
        if moves:
            conn = get_conn(read_only=False)
            try:
                starred = {s.ticker for s in list_sentinels(conn)}
            finally:
                conn.close()
            unstarred = [m for m in moves if m.symbol not in starred and not getattr(m, "is_theme_signal", False)]
            if unstarred:
                syms = [m.symbol for m in unstarred[:6]]
                st.info(f"🔥 今日 {len(unstarred)} 只重大异动票还没关注：{' / '.join(syms)}")
                if st.button("⭐ 一键关注这些异动票（自动盯回踩）", key="star_all_major_moves_today"):
                    conn = get_conn(read_only=False)
                    try:
                        for sym in syms:
                            add_sentinel(conn, sym)
                    finally:
                        conn.close()
                    st.success("已加入关注。之后跌到合理区间/大跌/均线附近会自动提醒。")
                    st.rerun()


def _fmt_brake_value(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.1f}{suffix}"


def _render_market_brake() -> None:
    try:
        snap = build_market_brake_snapshot()
    except Exception as exc:
        st.warning(f"市场情绪 / 估值刹车暂不可用：{exc}")
        return
    st.markdown("### 市场情绪 / 估值刹车")
    a, b, c = st.columns(3)
    with a:
        st.metric("VIX", _fmt_brake_value(snap.vix.value))
        st.caption(f"{snap.vix.status} · <=14 谨慎追高，>=30 恐慌机会")
    with b:
        delta = ""
        if snap.fear_greed.one_month_ago is not None and snap.fear_greed.value is not None:
            delta = f"1月前 {snap.fear_greed.one_month_ago:.0f} → 当前 {snap.fear_greed.value:.0f}"
        st.metric("Fear & Greed", _fmt_brake_value(snap.fear_greed.value))
        st.caption(f"{snap.fear_greed.status}" + (f" · {delta}" if delta else ""))
    with c:
        stale = " · 使用缓存/手动值" if snap.qqq_pe.stale else ""
        st.metric("QQQ PE", _fmt_brake_value(snap.qqq_pe.value))
        st.caption(f"{snap.qqq_pe.status}{stale} · >=38 科技估值警戒")
    st.info(f"当前结论：**{snap.status}** · {snap.conclusion}")
    st.caption(snap.disclaimer)


def _load_news_and_storylines(
    hours: int = 48,
    storyline_date: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ny = ZoneInfo("America/New_York")
    story_day = storyline_date or datetime.now(ny).date()
    story_key = story_day.isoformat()
    h = _clamp_recap_hours(hours)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat(timespec="seconds")
    conn = get_conn(read_only=True)
    try:
        news = pd.read_sql_query(
            """
            SELECT id, source, title, COALESCE(summary,'') AS summary, COALESCE(url,'') AS url,
                   published_at, COALESCE(primary_ticker,'') AS primary_ticker,
                   COALESCE(affected_tickers,'[]') AS affected_tickers,
                   COALESCE(cluster_id,'') AS cluster_id,
                   COALESCE(category,'AI 分析中') AS category,
                   COALESCE(severity,'pending') AS severity,
                   COALESCE(one_line_zh,'') AS one_line_zh,
                   COALESCE(impact_on_holdings,'') AS impact_on_holdings,
                   COALESCE(market_stance,'') AS market_stance,
                   COALESCE(what_it_means_zh,'') AS what_it_means_zh
            FROM news
            WHERE published_at >= ?
            ORDER BY published_at DESC
            LIMIT 500
            """,
            conn,
            params=(cutoff,),
        )
        storylines = pd.read_sql_query(
            """
            SELECT id, date, rank, title, COALESCE(narrative,'') AS narrative,
                   COALESCE(representative_tickers,'[]') AS representative_tickers,
                   COALESCE(tickers_with_changes,'[]') AS tickers_with_changes,
                   COALESCE(related_news_ids,'[]') AS related_news_ids,
                   COALESCE(impact_on_user_holdings,'无直接影响') AS impact_on_user_holdings,
                   COALESCE(early_movers,'[]') AS early_movers,
                   COALESCE(intensity,5) AS intensity
            FROM storylines
            WHERE date = ?
            ORDER BY rank ASC
            """,
            conn,
            params=(story_key,),
        )
    finally:
        conn.close()
    return news, storylines


def _load_source_health() -> pd.DataFrame:
    conn = get_conn(read_only=True)
    try:
        rows = pd.read_sql_query(
            """
            SELECT k, v, updated_at
            FROM meta_kv
            WHERE k LIKE 'news_source:%'
            ORDER BY k
            """,
            conn,
        )
    finally:
        conn.close()
    if rows.empty:
        return pd.DataFrame(columns=["source", "last_ok", "last_count", "last_error"])
    out: dict[str, dict[str, str]] = {}
    for _, r in rows.iterrows():
        k = str(r["k"])
        parts = k.split(":")
        if len(parts) != 3:
            continue
        src, metric = parts[1], parts[2]
        out.setdefault(src, {"source": src, "last_ok": "", "last_count": "0", "last_error": ""})
        out[src][metric] = str(r["v"] or "")
    return pd.DataFrame(list(out.values())).sort_values("source")


def _render_storylines(storylines: pd.DataFrame, news_df: pd.DataFrame, holdings: set[str], *, story_day: date) -> None:
    day_label = story_day.isoformat()
    st.markdown(f"#### 🔥 {day_label} 三大主线")
    if storylines.empty:
        st.warning("AI 主线暂未生成。下面先用重大异动雷达生成临时主线，不等 LLM 也能看。")
        try:
            moves = scan_market_radar(max_moves=12)
        except Exception:
            moves = []
        major = [m for m in moves if m.severity in {"urgent", "major"}]
        if major:
            tickers = " / ".join([f"{m.symbol} {m.day_change_pct:+.1f}%" for m in major[:6]])
            semis = [m for m in major if any(k in " ".join(m.themes).lower() for k in ("memory", "semiconductor", "hbm", "ai", "data center"))]
            title = "AI 半导体 / 数据中心异动" if len(semis) >= 2 else "今日重大异动集中爆发"
            with st.container(border=True):
                st.markdown(f"**🔥 临时主线：{title}**")
                st.write(f"发生了什么：{tickers}。")
                st.write("为什么重要：这是规则层从实时价格里抓到的同步异动，不是 LLM 编出来的主线。它提醒你主题在动，但不代表现在能追。")
                st.write("对你的钱：如果你持有或关注 AI/半导体/数据中心链条，今天应该把相关标的加入关注，让系统盯回踩。")
                st.caption("主线生成失败时显示这块；LLM 恢复后会替换为正式主线。")
        _render_latest_digest(news_df, holdings)
        return
    news_map = {str(r["id"]): r for _, r in news_df.iterrows()}
    for _, s in storylines.iterrows():
        tickers = _parse_js(s["representative_tickers"], [])
        early = _parse_js(s["early_movers"], [])
        related = _parse_js(s["related_news_ids"], [])
        intensity = _safe_intensity(s["intensity"])
        with st.container(border=True):
            st.markdown(f"**🔥 主线 {int(s['rank'])}：{s['title']}** ｜ 热度 {'█' * max(1, min(10, intensity))}")
            st.write(str(s["narrative"]))
            if tickers:
                st.caption(f"代表标的：{' / '.join([str(x) for x in tickers[:5]])}")
            st.write(f"💼 对你持仓影响：{s['impact_on_user_holdings'] or '无直接影响'}")
            movers = []
            for x in early:
                t = str((x or {}).get("ticker") or "").upper()
                ch = float((x or {}).get("change_pct") or 0.0)
                if not t or abs(ch) >= 2.0:
                    continue
                movers.append(f"{t} {ch:+.2f}%")
            if movers:
                st.caption("🆕 早期机会：" + " · ".join(movers[:5]))
            st.markdown("📰 相关新闻")
            for nid in related[:5]:
                n = news_map.get(str(nid))
                if n is None:
                    continue
                t = str(n["published_at"])[11:16]
                src = str(n["source"]).capitalize()
                title = str(n["title"])
                url = str(n["url"] or "")
                if url:
                    st.markdown(f"- [{t} {src}] [{title}]({url})")
                else:
                    st.markdown(f"- [{t} {src}] {title}")


def _render_card_wall(df: pd.DataFrame, holdings: set[str]) -> None:
    st.markdown("#### 🧱 卡片墙")
    if df.empty:
        st.info("当前筛选下暂无新闻。")
        return
    for _, r in df.head(80).iterrows():
        sev = str(r["severity"])
        affected = [str(x).upper() for x in _parse_js(r["affected_tickers"], [])]
        hit = [x for x in affected if x in holdings]
        with st.container(border=True):
            stance = str(r.get("market_stance") or "").strip().lower()
            stance_txt = _STANCE_LABEL.get(stance, "")
            header_bits = []
            if stance_txt:
                header_bits.append(stance_txt)
            header_bits.append(
                f"{_SEV_EMOJI.get(sev, '•')} {_SEV_LABEL.get(sev, sev)} · {r['category']} · {str(r['published_at'])[11:16]}"
            )
            st.markdown(f"**{' · '.join(header_bits)}**")
            st.markdown(f"**{r['title']}**")
            witm = _clean_ai_text(r.get("what_it_means_zh"))
            if witm:
                st.info(f"💡 {witm}")
            elif str(r["one_line_zh"]).strip():
                st.write(str(r["one_line_zh"]))
            elif sev == "pending":
                st.write("⏳ AI 分析中，下一轮打标后会补上人话总结和持仓影响。")
            if str(r["summary"]).strip():
                st.caption(str(r["summary"])[:200])
            impact = _clean_ai_text(r["impact_on_holdings"])
            if impact:
                st.write(f"💼 {impact}")
            elif hit:
                st.write(f"💼 持仓相关：{', '.join(hit)}")
            else:
                st.write("⏳ 待 AI 分析")
            with st.expander("完整分析", expanded=False):
                st.write(f"来源：{r['source']}")
                st.write(f"时间：{r['published_at']}")
                st.write(f"事件簇：{r['cluster_id'] or r['id']}")
                st.write(f"关联标的：{', '.join(affected) if affected else '无'}")
                st.write(f"摘要：{r['summary'] or '无'}")
            if str(r["url"]).strip():
                st.link_button("查看原文", str(r["url"]))


def _render_latest_digest(df: pd.DataFrame, holdings: set[str]) -> None:
    st.markdown("#### 最新真实新闻快览")
    if df.empty:
        st.info("当前筛选下暂无新闻。")
        return
    holding_rows = []
    general_rows = []
    for _, r in df.head(80).iterrows():
        if _direct_holding_matches(r, holdings):
            holding_rows.append(r)
        else:
            general_rows.append(r)

    def _line(r: pd.Series) -> str:
        sev = str(r["severity"])
        stance = str(r.get("market_stance") or "").strip().lower()
        stance_txt = _STANCE_LABEL.get(stance, "")
        t = str(r["published_at"])[11:16]
        src = str(r["source"])
        title = str(r.get("what_it_means_zh") or r["one_line_zh"] or r["title"])
        url = str(r["url"] or "")
        prefix = f"{_SEV_EMOJI.get(sev, '•')} {t} · {src}"
        if stance_txt:
            prefix = f"{stance_txt} · {prefix}"
        return f"- {prefix} · [{title}]({url})" if url else f"- {prefix} · {title}"

    left, right = st.columns(2)
    with left:
        st.caption("持仓相关")
        rows = holding_rows[:10]
        if not rows:
            st.write("暂无持仓相关新闻。")
        for r in rows:
            st.markdown(_line(r))
    with right:
        st.caption("市场/观察池")
        rows = general_rows[:10]
        if not rows:
            st.write("暂无其它新闻。")
        for r in rows:
            st.markdown(_line(r))

    with st.expander("展开卡片墙", expanded=False):
        _render_card_wall(df.head(20), holdings)


def _render_fetch_visibility(news: pd.DataFrame, holdings: set[str], *, hours: int) -> None:
    st.markdown("#### 抓取结果可视化")
    h = _clamp_recap_hours(hours)
    if news.empty:
        st.warning(f"当前库里没有近 {h} 小时新闻。先点「抓真实新闻」，或把上面的「新闻回顾时长」调大后再看。")
        return

    s1, s2, s3 = st.columns(3)
    by_source = news.groupby("source", as_index=False).size().sort_values("size", ascending=False)
    hold_hits = int(news.apply(lambda r: bool(_direct_holding_matches(r, holdings)), axis=1).sum())
    s1.metric("来源数", int(by_source["source"].nunique()))
    s2.metric(f"总条数({h}h)", len(news))
    s3.metric("持仓相关", hold_hits)

    st.caption("来源分布（你能直观看到到底抓到了哪些源）")
    st.dataframe(
        by_source.rename(columns={"source": "来源", "size": "条数"}),
        hide_index=True,
        use_container_width=True,
    )

    preview = news[["published_at", "source", "primary_ticker", "title", "severity"]].head(20).rename(
        columns={
            "published_at": "时间",
            "source": "来源",
            "primary_ticker": "主标的",
            "title": "标题",
            "severity": "级别",
        }
    )
    st.caption("最新 20 条抓取预览（确认不是空跑）")
    st.dataframe(preview, hide_index=True, use_container_width=True)


def _macro_crude_chg_pct(macro: object) -> float:
    """兼容旧版 MacroStrip（无 crude_chg_pct 字段）。"""
    return float(getattr(macro, "crude_chg_pct", 0.0) or 0.0)


def _market_regime_text(spy: float, qqq: float, vix: float, crude: float = 0.0, ten_year: float = 0.0) -> str:
    pressure = []
    if crude >= 3:
        pressure.append("油价上行")
    if ten_year >= 4.6:
        pressure.append("10Y 偏高")
    suffix = f"；{'、'.join(pressure)}" if pressure else ""
    if spy >= 0.5 and qqq >= 0.5 and vix <= 20:
        return f"risk-on（偏进攻）{suffix}"
    if spy <= -0.8 or qqq <= -1.0 or vix >= 26:
        return f"risk-off（偏防守）{suffix}"
    return f"mixed（震荡）{suffix}"


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", text, flags=re.I):
            return True
    return False


def _source_quality_score(source: str) -> int:
    src = source.lower()
    if "reuters" in src:
        return 10
    if "associated press" in src or "barrons" in src:
        return 7
    if "mt newswires" in src or "investing.com" in src:
        return 5
    if any(h in src for h in _GENERIC_SOURCE_HINTS):
        return -4
    return 2


def _title_key(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()[:120]


def _is_generic_article(text: str) -> bool:
    generic_patterns = (
        r"\bis .{1,80} a buy\b",
        r"\bshould you buy\b",
        r"\bbetter .{1,80} stock\b",
        r"\btop .{1,60} stocks\b",
        r"\bstocks of the week\b",
        r"\btime to sell\b",
        r"\bwant .{1,60} stock before\b",
    )
    return any(re.search(p, text, flags=re.I) for p in generic_patterns)


def _dedupe_radar(items: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in sorted(items, key=lambda x: int(x.get("priority_score") or 0), reverse=True):
        key = _title_key(str(item.get("source_title") or item.get("text") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _radar_item_from_row(
    r: pd.Series,
    *,
    category: str,
    reason: str,
    related_symbols: list[str] | None = None,
    confidence: str = "medium",
    priority_score: int = 0,
) -> dict:
    title = str(r.get("title") or "")
    zh = str(r.get("one_line_zh") or "").strip()
    impact = str(r.get("impact_on_holdings") or "").strip()
    sev = str(r.get("severity") or "pending")
    return {
        "text": zh or title,
        "is_pending_ai": not bool(zh),
        "impact_on_holdings": impact,
        "source_title": title,
        "source_url": str(r.get("url") or ""),
        "published_at": str(r.get("published_at") or ""),
        "source_name": str(r.get("source") or ""),
        "related_symbols": related_symbols or [],
        "category": category,
        "severity": sev,
        "confidence": confidence,
        "reason": reason,
        "matched_symbols": related_symbols or [],
        "priority_score": int(priority_score),
    }


def _render_radar_section(title: str, items: list[dict], empty_text: str) -> None:
    st.markdown(f"#### {title}")
    if not items:
        st.caption(empty_text)
        return
    for item in items:
        label = str(item.get("text") or item.get("source_title") or "").strip()
        symbols = item.get("related_symbols") or item.get("matched_symbols") or []
        who = f" · {', '.join(map(str, symbols))}" if symbols else ""
        sev = str(item.get("severity") or "")
        conf = str(item.get("confidence") or "medium")
        url = str(item.get("source_url") or "")
        source = str(item.get("source_name") or "market_pulse")
        published = str(item.get("published_at") or "")
        time_part = published[11:16] if len(published) >= 16 else "实时"
        impact = _clean_ai_text(item.get("impact_on_holdings"))
        category = str(item.get("category") or "unknown")
        pending = bool(item.get("is_pending_ai"))
        with st.container(border=True):
            st.caption(f"{_SEV_EMOJI.get(sev, '•')} {sev or conf}{who}")
            if url:
                st.markdown(f"**{'⏳ ' if pending else ''}[{label}]({url})**")
            else:
                st.markdown(f"**{'⏳ ' if pending else ''}{label}**")
            if impact:
                st.write(f"💼 {impact}")
            elif category in {"macro", "policy", "market_pulse"}:
                st.write("💼 宏观影响：你科技仓较重，利率/油价/美元变化主要通过估值和风险偏好影响组合，不代表个股基本面立刻变坏。")
            elif symbols:
                st.write(f"💼 {', '.join(map(str, symbols))} 被直接提到；先按信息提醒处理，暂不推导买卖动作。")
            else:
                st.write("⏳ 待 AI 分析")
            st.caption(f"📌 来源: {source} · {time_part} · {category}")
            if item.get("source_title") and str(item.get("source_title")) != label:
                st.caption(f"原题: {item.get('source_title')}")


def _market_alert_items(macro) -> list[dict]:
    items: list[dict] = []
    crude = _macro_crude_chg_pct(macro)
    if abs(crude) >= 3:
        items.append(
            {
                "text": f"油价今日 {crude:+.2f}%，通胀/能源风险需要看一眼",
                "category": "market_pulse",
                "confidence": "high",
                "reason": "结构化油价信号",
                "priority_score": 35,
            }
        )
    if float(macro.vix or 0.0) >= 26:
        items.append(
            {
                "text": f"VIX 升至 {macro.vix:.1f}，市场风险偏好明显降温",
                "category": "market_pulse",
                "confidence": "high",
                "reason": "结构化波动率信号",
                "priority_score": 34,
            }
        )
    if float(macro.ten_year_yield_pct or 0.0) >= 4.6:
        items.append(
            {
                "text": f"10Y 美债约 {macro.ten_year_yield_pct:.2f}%，成长股估值压力偏高",
                "category": "market_pulse",
                "confidence": "high",
                "reason": "结构化利率信号",
                "priority_score": 30,
            }
        )
    return items


def _radar_lists(news: pd.DataFrame, holdings: set[str], watchlist: set[str], macro_strip=None) -> dict[str, list[dict]]:
    important: list[dict] = []
    mine: list[dict] = []
    macro: list[dict] = []
    missed: list[dict] = []
    if macro_strip is not None:
        important.extend(_market_alert_items(macro_strip))
    if news.empty:
        return {"important": _dedupe_radar(important, 2), "mine": mine, "macro": macro, "missed": missed}

    macro_kw = (
        "fed",
        "federal reserve",
        "rate",
        "rates",
        "cpi",
        "ppi",
        "jobs",
        "payrolls",
        "oil",
        "crude",
        "war",
        "middle east",
        "treasury",
        "yield",
        "yields",
        "dollar",
        "vix",
        "tariff",
        "export control",
    )
    earnings_kw = ("earnings", "results", "guidance", "revenue", "eps", "beat", "beats", "miss", "misses")
    analyst_kw = ("upgrade", "downgrade", "price target")
    move_kw = ("jumped", "jumps", "surged", "plunged", "rallies", "falls", "slumps", "movers")
    reval_kw = (
        "spinoff",
        "separation",
        "index inclusion",
        "data center",
        "datacenter",
        "electricity demand",
        "power demand",
        "ai demand",
        "supply shortage",
        "cycle recovery",
    )
    generic_kw = ("stocks of the week", "is the stock a buy", "time to sell", "better stock", "want spacex stock")

    for _, r in news.iterrows():
        title = str(r.get("title") or "")
        summary = str(r.get("summary") or "")
        text = f"{title} {summary}".lower()
        source_name = str(r.get("source") or "")
        sev = str(r.get("severity") or "pending")
        direct = _direct_holding_matches(r, holdings)
        watch_direct = _direct_holding_matches(r, watchlist)
        source_score = _source_quality_score(source_name)
        is_generic = _has_phrase(text, generic_kw) or _has_phrase(text, _GENERIC_TITLE_HINTS) or _is_generic_article(text)
        generic_penalty = 35 if is_generic else 0
        severity_bonus = 12 if sev in {"urgent", "important"} else (4 if sev == "attention" else 0)
        price_move = _has_phrase(text, move_kw)
        earnings = _has_phrase(text, earnings_kw)
        analyst = _has_phrase(text, analyst_kw)
        reval = _has_phrase(text, reval_kw)
        macro_hit = _has_phrase(text, macro_kw)

        if direct:
            score = 40 + source_score + severity_bonus - generic_penalty
            if price_move:
                score += 20
            if earnings:
                score += 20
            if analyst:
                score += 12
            if reval:
                score += 15
            item = _radar_item_from_row(
                r,
                category="holding_direct",
                reason="标题/摘要直接提到你的持仓",
                related_symbols=direct,
                confidence="high",
                priority_score=score,
            )
            if sev == "urgent":
                important.append(item)
            mine.append(item)
            continue

        if macro_hit and not is_generic:
            score = 25 + source_score + severity_bonus
            if price_move:
                score += 8
            item = _radar_item_from_row(
                r,
                category="macro",
                reason="宏观关键词命中",
                related_symbols=[],
                confidence="high" if source_score >= 5 else "medium",
                priority_score=score,
            )
            macro.append(item)
            if sev == "urgent":
                important.append(item)
            continue

        if watch_direct and not is_generic and (earnings or analyst or reval or price_move):
            score = 18 + source_score + severity_bonus - generic_penalty
            if reval:
                score += 15
            if earnings:
                score += 20
            item = _radar_item_from_row(
                r,
                category="watchlist",
                reason="观察池出现财报/评级/重估/异动信号",
                related_symbols=watch_direct,
                confidence="medium",
                priority_score=score,
            )
            missed.append(item)
            continue

        if reval and not is_generic:
            score = 15 + source_score + severity_bonus
            item = _radar_item_from_row(
                r,
                category="holding_sector",
                reason="行业/主题重估信号，但未直接提到持仓",
                related_symbols=[],
                confidence="medium",
                priority_score=score,
            )
            missed.append(item)

    important_out = _dedupe_radar(important, 2)
    used = {_title_key(str(x.get("source_title") or x.get("text") or "")) for x in important_out}
    mine_out = _dedupe_radar(
        [x for x in mine if _title_key(str(x.get("source_title") or x.get("text") or "")) not in used],
        3,
    )
    used.update(_title_key(str(x.get("source_title") or x.get("text") or "")) for x in mine_out)
    macro_out = _dedupe_radar(
        [x for x in macro if _title_key(str(x.get("source_title") or x.get("text") or "")) not in used],
        2,
    )
    used.update(_title_key(str(x.get("source_title") or x.get("text") or "")) for x in macro_out)
    missed_out = _dedupe_radar(
        [x for x in missed if _title_key(str(x.get("source_title") or x.get("text") or "")) not in used],
        2,
    )
    return {"important": important_out, "mine": mine_out, "macro": macro_out, "missed": missed_out}


def render() -> None:
    st.subheader("📰 今日市场雷达")
    st.caption("默认只看会影响你资金的大方向（5-8 条）；抓取控制台在下方“高级工作台”。")

    ny_tz = ZoneInfo("America/New_York")
    if "news_recap_hours" not in st.session_state:
        st.session_state["news_recap_hours"] = 48
    if "news_storyline_date" not in st.session_state:
        st.session_state["news_storyline_date"] = datetime.now(ny_tz).date()

    c_tl, c_td = st.columns([3, 1])
    with c_tl:
        st.radio(
            "新闻回顾时长（小时）",
            options=list(_RECAP_HOUR_CHOICES),
            horizontal=True,
            key="news_recap_hours",
            help="从数据库读取最近多少小时内的新闻，用于雷达与下方预览。固定档位，避免手动输入和时区边界错位。",
        )
    with c_td:
        st.date_input(
            "主线日期",
            min_value=date(2020, 1, 1),
            max_value=datetime.now(ny_tz).date(),
            key="news_storyline_date",
            help="对应库表 `storylines.date`（生成主线当天的 UTC/本地日历日）。与「新闻回顾时长」独立。",
        )

    recap_h = _clamp_recap_hours(int(st.session_state.get("news_recap_hours", 48)))
    story_d = st.session_state["news_storyline_date"]
    if not isinstance(story_d, date):
        story_d = datetime.now(ny_tz).date()
        st.session_state["news_storyline_date"] = story_d

    holdings = {s.upper() for s in load_watchlist_tickers()}
    news, stories = _load_news_and_storylines(hours=recap_h, storyline_date=story_d)
    watchlist = {s.upper() for s in load_opportunity_tickers()} - holdings
    _render_today_actions()
    _render_market_brake()
    render_major_moves(limit=6)
    macro_fn = getattr(macro_data, "snapshot_macro_view", None) or getattr(macro_data, "snapshot_macro")
    macro = macro_fn()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("SPY", f"{macro.spy_chg_pct:+.2f}%")
    c2.metric("QQQ", f"{macro.qqq_chg_pct:+.2f}%")
    c3.metric("VIX", f"{macro.vix:.1f}")
    c4.metric("10Y", f"{macro.ten_year_yield_pct:.2f}%")
    crude_pct = _macro_crude_chg_pct(macro)
    c5.metric("Oil", f"{crude_pct:+.2f}%")
    c6.metric("DXY", f"{macro.dxy:.1f}")
    st.caption(
        f"市场温度：**{_market_regime_text(macro.spy_chg_pct, macro.qqq_chg_pct, macro.vix, crude_pct, macro.ten_year_yield_pct)}**"
    )

    _render_storylines(stories, news, holdings, story_day=story_d)

    radar = _radar_lists(news, holdings, watchlist, macro)
    _render_radar_section("🔴 重要提醒", radar["important"], "今日暂无高优先级直接持仓风险。")
    _render_radar_section("🟡 我的持仓", radar["mine"], "今日没有高优先级持仓新闻。")
    _render_radar_section("🌍 大方向", radar["macro"], "宏观面暂无新增高优先级事件。")
    _render_radar_section("✨ 不要错过", radar["missed"], "今天没有明确的早期机会触发。")

    with st.expander("高级工作台（抓取/打标/主线/调试）", expanded=False):
        b1, b2, b3, note = st.columns([1, 1, 1, 4])
        with b1:
            if st.button("抓真实新闻", use_container_width=True):
                try:
                    tickers = sorted({*load_watchlist_tickers(), *load_opportunity_tickers()})
                    run_news_pipeline = _load_fresh_news_pipeline()
                    stats = run_news_pipeline(default_db_path(), tickers, days_back=1)
                    st.session_state["news_fetch_stats"] = stats
                    st.success(f"抓取 {stats.get('fetched', 0)} / 去重后 {stats.get('after_dedupe', 0)} / 新增 {stats.get('inserted', 0)}")
                except Exception as e:
                    st.error(f"新闻抓取失败：{e}")
                    st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        with b2:
            if st.button("AI 打标签", use_container_width=True):
                try:
                    run_news_tagging = _load_fresh_news_tagger()
                    stats = run_news_tagging(default_db_path(), load_watchlist_tickers())
                    st.session_state["news_tag_stats"] = stats
                    st.info(f"待处理 {stats.get('pending', 0)} / 已打标 {stats.get('tagged', 0)} / 错误批次 {stats.get('errors', 0)}")
                except Exception as e:
                    st.error(f"AI 打标签失败：{e}")
        with b3:
            if st.button("生成主线", use_container_width=True):
                try:
                    story_mod = _load_fresh_storyline_module()
                    candidates = story_mod.get_top_news_for_storyline(default_db_path())
                    stats = story_mod.run_storyline_pipeline(default_db_path(), load_watchlist_tickers())
                    stats["candidate_news"] = len(candidates)
                    st.session_state["news_story_stats"] = stats
                    st.info(f"候选新闻 {len(candidates)} / 主线 status={stats.get('status','n/a')} count={stats.get('count',0)}")
                except Exception as e:
                    st.error(f"主线生成失败：{e}")
        with note:
            st.caption("默认不依赖 LLM。你只在需要深度分析时再手动点击 AI 打标签 / 生成主线。")

        render_alert_debug()

        if st.button("生成并推送今日新闻摘要（按需）", use_container_width=True):
            run_daily_news_push_once = _load_fresh_daily_news_push()
            out = run_daily_news_push_once()
            st.session_state["daily_news_digest"] = out.get("digest") or {}
            st.success(f"已推送 {out.get('pushed', 0)} 条。")

        _render_fetch_visibility(news, holdings, hours=recap_h)
        with st.expander("数据源健康状态", expanded=False):
            health = _load_source_health()
            if health.empty:
                st.caption("暂无健康状态记录，先点一次“抓真实新闻”。")
            else:
                st.dataframe(
                    health.rename(columns={"source": "来源", "last_ok": "最近成功时间", "last_count": "最近抓取条数", "last_error": "最近错误"}),
                    hide_index=True,
                    use_container_width=True,
                )
