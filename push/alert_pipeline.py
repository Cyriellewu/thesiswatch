from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _now_iso_local() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def materialize_alert_from_signal(conn: sqlite3.Connection, sig_id: int) -> str | None:
    """若可生成告警则 INSERT OR IGNORE，返回 alerts.dedupe_key；未插入返回 None。"""

    row = conn.execute(
        """SELECT id, symbol, signal_type, payload_json, score, created_at
           FROM market_signals WHERE id = ?""",
        (sig_id,),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload_json"] or "{}")
    sym = row["symbol"]
    stype = row["signal_type"]

    dedupe_hint = str(payload.get("dedupe_hint") or "").strip()
    if not dedupe_hint:
        dedupe_hint = f"sig:{sig_id}"

    occurred = str(row["created_at"] or _now_iso_local())
    lvl: str
    category: str
    title: str
    what: str
    why: str
    rel: str
    watch: str
    risk: str

    if stype == "daily_price_move":
        lvl = str(payload.get("implied_alert_level") or "attention")
        bucket = str(payload.get("bucket") or "holding")
        chg = float(payload.get("chg_pct") or 0)
        category = "opportunity" if bucket == "opportunity" else "holding"
        title = f"{'持仓' if category == 'holding' else '机会'}波动：{sym}"
        what = f"{sym} 相对前一交易日收盘大约 {chg:+.2f}%。"
        why = "波动超过设定的关注线，值得确认是不是有消息或情绪在驱动。"
        rel = (
            "这是你在 watchlist 里的真实持仓之一，会直接影响账户。"
            if category == "holding"
            else "这只不在持仓里，但在机会观测名单。"
        )
        watch = "快速看一眼相关新闻标题和成交量有没有配合价格波动。"
        risk = "急涨也可能快速回撤；不要被单日涨幅牵着追价。"
    elif stype == "company_headline":
        lvl = str(payload.get("implied_alert_level") or "attention")
        category = "holding"
        title = f"持仓新闻：{sym}"
        what = f"出现一条相关新闻：{payload.get('title') or '（无标题）'}"
        why = "持仓标的出现新消息时，股价往往更容易波动，需要先搞清利多还是利空。"
        rel = f"你持有 {sym}，所以它和你账户直接相关。"
        watch = "关注是否涉及财报、监管、并购、裁员、大额合同等实质信息。"
        risk = "单条标题可能不完整；别把一条新闻当成必须买卖的理由。"
    elif stype == "macro_strip":
        lvl = str(payload.get("_level") or "routine")
        if lvl == "routine":
            return None
        strip = payload
        category = "macro"
        title = "市场整体温度"
        spyc = float(strip.get("spy_chg_pct") or 0)
        qqqc = float(strip.get("qqq_chg_pct") or 0)
        what = (
            f"VIX≈{strip.get('vix')} ，SPY 约 {spyc:+.2f}% ，"
            f"QQQ 约 {qqqc:+.2f}% ，10Y≈{strip.get('ten_year_yield_pct')}% 。"
        )
        why = "风险偏好环境会影响高波动和成长风格；需要先判断今天在哪个区间。"
        rel = "会间接影响到你科技股占比偏高的持仓组合。"
        watch = "当你看到 VIX 上行且指数偏弱时，把「防回撤」优先级提高。"
        risk = "宏观数据有噪声和滞后；它提示环境风险，不提供个股买卖时点。"
    elif stype in {
        "price_breakout",
        "volume_spike",
        "earnings_beat",
        "analyst_upgrade",
        "insider_buy",
        "signal_bundle",
    }:
        lvl = str(payload.get("implied_alert_level") or "attention")
        bucket = str(payload.get("bucket") or "").lower()
        category = "opportunity" if bucket == "opportunity" else ("holding" if (sym or "").upper() else "opportunity")
        title = str(payload.get("title") or f"{stype}:{sym or 'MARKET'}")
        what = str(payload.get("what") or payload.get("blurb") or "出现新的结构化信号。")
        why = str(payload.get("why") or "多信号共振时，值得优先复盘。")
        rel = str(payload.get("relation") or "用于虚拟账户与提醒，不触发真实下单。")
        watch = str(payload.get("watch") or "观察后续价格延续性与新闻确认。")
        risk = str(payload.get("risk") or "单一信号也可能失效，注意控制仓位。")
    else:
        return None

    cur = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (level, category, symbol, title, what_happened, why_matters, relation_to_you, watch_next, risk, signal_ids, dedupe_key, occurred_at, pushed_ntfy)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            lvl,
            category,
            sym,
            title,
            what,
            why,
            rel,
            watch,
            risk,
            json.dumps([sig_id], ensure_ascii=False),
            dedupe_hint,
            occurred,
        ),
    )
    return dedupe_hint if cur.rowcount else None


def materialize_signals_batch(conn: sqlite3.Connection, signal_ids: list[int]) -> int:
    n = 0
    for sid in signal_ids:
        if materialize_alert_from_signal(conn, sid):
            n += 1
    return n


def flush_pending_ntfy(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
    levels: tuple[str, ...] | None = None,
) -> int:
    """按优先级推送未发送的告警到 ntfy；返回发送条数。"""

    from push.notify import send_alert

    if levels:
        ph = ",".join(["?"] * len(levels))
        rows = conn.execute(
            f"""SELECT id, level, title, what_happened, why_matters, risk, dedupe_key
               FROM alerts
               WHERE pushed_ntfy = 0 AND level IN ({ph})
               ORDER BY CASE level WHEN 'urgent' THEN 0 WHEN 'major' THEN 1 WHEN 'attention' THEN 2 ELSE 3 END,
                        occurred_at ASC
               LIMIT ?""",
            (*levels, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, level, title, what_happened, why_matters, risk, dedupe_key
               FROM alerts
               WHERE pushed_ntfy = 0
               ORDER BY CASE level WHEN 'urgent' THEN 0 WHEN 'major' THEN 1 WHEN 'attention' THEN 2 ELSE 3 END,
                        occurred_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    n = 0
    for row in rows:
        body_lines = []
        if row["what_happened"]:
            body_lines.append(f"发生了什么：{row['what_happened']}")
        if row["why_matters"]:
            body_lines.append(f"为什么重要：{row['why_matters']}")
        if row["risk"]:
            body_lines.append(f"风险：{row['risk']}")
        body = "\n".join(body_lines) or row["title"]
        ok = send_alert(str(row["level"]), str(row["title"]), body)
        if ok:
            conn.execute("UPDATE alerts SET pushed_ntfy = 1 WHERE id = ?", (row["id"],))
            n += 1
    return n
