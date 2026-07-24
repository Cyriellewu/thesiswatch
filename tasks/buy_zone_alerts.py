"""价格区间（price_zone）与书籍纪律（book_system）写入 `alerts`；含已持仓 STOP / 减仓 同步。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from db.client import get_conn
from signals.book_card_context import build_book_card_view
from stock_picker.zone_context import etf_extra_band, instrument_kind


def _utc_ts() -> datetime:
    return datetime.now(timezone.utc)


def _cooldown_bucket(cooldown_hours: int) -> int:
    h = max(1, int(cooldown_hours))
    return int(_utc_ts().timestamp() // (h * 3600))


def _skip_symbol(sym: str, px: float) -> bool:
    u = sym.upper()
    if u.endswith((".KS", ".JP", ".T", ".HK")):
        return True
    if px <= 0 or px > 50_000:
        return True
    return False


def _insert(
    conn: Any,
    *,
    category: str = "price_zone",
    level: str,
    symbol: str,
    title: str,
    what: str,
    why: str,
    relation: str,
    watch: str,
    risk: str,
    dedupe: str,
    occurred_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO alerts
        (level, category, symbol, title, what_happened, why_matters, relation_to_you, watch_next, risk, dedupe_key, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (level, category, symbol, title, what, why, relation, watch, risk, dedupe, occurred_at),
    )
    return int(cur.rowcount or 0)


def sync_price_zone_alerts(
    rows: Sequence[Any], *, cooldown_hours: int = 24, account_equity: float = 25_000.0
) -> int:
    """对当前候选行扫描现价与区间，命中则 INSERT OR IGNORE。返回本轮新增条数。"""
    if not rows:
        return 0
    now = _utc_ts()
    occurred_at = now.strftime("%Y-%m-%d %H:%M:%S")
    cb = _cooldown_bucket(cooldown_hours)
    inserted = 0
    conn = get_conn(read_only=False)
    try:
        for row in rows:
            sym = str(getattr(row, "symbol", "") or "").strip().upper()
            if not sym:
                continue
            try:
                px = float(getattr(row, "price", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if _skip_symbol(sym, px):
                continue
            zones = getattr(row, "zones", None)
            if zones is None:
                continue
            try:
                rec_lo = float(getattr(zones, "recommended_low", 0.0) or 0.0)
                rec_hi = float(getattr(zones, "recommended_high", 0.0) or 0.0)
                comf = float(getattr(zones, "comfortable_buy", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if rec_hi <= 0 or rec_lo <= 0:
                continue

            bucket = str(getattr(row, "bucket", "") or "")
            stype = str(getattr(row, "stock_type", "") or "")
            kind = instrument_kind(sym, bucket, stype)
            t_score = int(getattr(row, "t_score", 0) or 0)

            if kind == "etf":
                ma200 = float(getattr(row, "ma200", 0.0) or 0.0)
                if ma200 <= 0:
                    continue
                band = etf_extra_band(ma200)
                ex_lo, ex_hi = band["extra_low"], band["extra_high"]
                if ex_lo <= px <= ex_hi:
                    dedupe = f"pz:etf-extra:{sym}:{cb}"
                    title = f"[ETF 额外加仓带] {sym} 已进入 MA200 附加仓区间"
                    what = (
                        f"{sym} 当前 ${px:,.2f}，落在额外加仓带 ${ex_lo:,.2f}-${ex_hi:,.2f} "
                        f"（≈ MA200×[0.90,1.03]）。定投仍按周计划执行，此为「额外加仓」参考。"
                    )
                    why = "价格回到长期均线附近的附加仓窗口；不等同于必须加仓。"
                    relation = "若你启用 ntfy 且已收藏该标的，可配合哨兵推送（与 price_zone 独立去重）。"
                    watch = "关注是否跌破带下沿或基本面变化；勿机械加码。"
                    risk = "ETF 仍可能继续下跌；注意整体仓位与现金流。"
                    inserted += _insert(
                        conn,
                        category="price_zone",
                        level="major",
                        symbol=sym,
                        title=title,
                        what=what,
                        why=why,
                        relation=relation,
                        watch=watch,
                        risk=risk,
                        dedupe=dedupe,
                        occurred_at=occurred_at,
                    )
                continue

            # 非 ETF：更深回踩 → 跌破 typed 下沿 → 区间内
            if comf > 0 and px <= comf:
                dedupe = f"pz:comfort:{sym}:{cb}"
                zname = "观察区间" if t_score < 4 else "推荐区间"
                title = f"[更舒服回踩] {sym} 已触及/低于舒适回踩位"
                what = (
                    f"{sym} 当前 ${px:,.2f}，舒适回踩约 ${comf:,.2f}；"
                    f"typed {zname}下沿 ${rec_lo:,.2f}、上沿 ${rec_hi:,.2f}。"
                )
                why = "更深回撤往往意味着波动或情绪更极端；结合趋势分与新闻再决定是否分批。"
                relation = "未持仓：小步试错；已持仓：对照持仓纪律，避免越跌越买成瘾。"
                watch = "若随后跌破关键均线或基本面恶化，应转为防守。"
                risk = "接飞刀风险；T<4 时仅观察或小仓。"
                inserted += _insert(
                    conn,
                    category="price_zone",
                    level="major",
                    symbol=sym,
                    title=title,
                    what=what,
                    why=why,
                    relation=relation,
                    watch=watch,
                    risk=risk,
                    dedupe=dedupe,
                    occurred_at=occurred_at,
                )
                continue

            if px < rec_lo:
                dedupe = f"pz:break:{sym}:{cb}"
                title = f"[跌破区间下沿] {sym} 已低于 typed 区间下沿"
                what = f"{sym} 当前 ${px:,.2f}，低于区间下沿 ${rec_lo:,.2f}（上沿 ${rec_hi:,.2f}）。"
                why = "跌破下沿可能意味着趋势或叙事转弱，或仅是正常波动；需要复核而非机械补仓。"
                relation = "先检查趋势/基本面/仓位上限，再决定减仓、持有或极小仓左侧试错。"
                watch = "关注能否数日收回区间；放量跌破均线需更谨慎。"
                risk = "越跌越买在趋势破坏时可能放大亏损。"
                inserted += _insert(
                    conn,
                    category="price_zone",
                    level="attention",
                    symbol=sym,
                    title=title,
                    what=what,
                    why=why,
                    relation=relation,
                    watch=watch,
                    risk=risk,
                    dedupe=dedupe,
                    occurred_at=occurred_at,
                )
                continue

            if rec_lo <= px <= rec_hi:
                view = build_book_card_view(row, account_equity=account_equity)
                if view.system_buyable:
                    continue
                dedupe = f"pz:zone-observe:{sym}:{cb}"
                title = f"[typed 观察] {sym} 在区间内但系统买点未齐备"
                blk = "；".join(view.system_buy_blockers[:6]) or "趋势/环境/盈亏比/量价等未同时满足。"
                what = f"{sym} 当前 ${px:,.2f}，typed 带 ${rec_lo:,.2f}-${rec_hi:,.2f}。未满足：{blk}"
                why = "Murphy/Minervini/Elder 合成门：价格位置只是必要条件，不是充分条件。"
                relation = "页面「可分批」若与系统冲突，以本块为准：先观察或极小仓。"
                watch = "等待趋势模板 + 风险收益比 + 大盘环境共振。"
                risk = "跌进区间不等于便宜买好货；警惕接刀与叙事坍塌。"
                inserted += _insert(
                    conn,
                    category="price_zone",
                    level="attention",
                    symbol=sym,
                    title=title,
                    what=what,
                    why=why,
                    relation=relation,
                    watch=watch,
                    risk=risk,
                    dedupe=dedupe,
                    occurred_at=occurred_at,
                )

        conn.commit()
    finally:
        conn.close()
    return inserted


def fetch_recent_price_zone_alerts(limit: int = 8) -> list[dict[str, str]]:
    conn = get_conn(read_only=True)
    try:
        cur = conn.execute(
            """
            SELECT title, what_happened, occurred_at, symbol, category
            FROM alerts
            WHERE category IN ('price_zone', 'book_system')
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        out: list[dict[str, str]] = []
        for r in cur.fetchall():
            out.append(
                {
                    "title": str(r["title"] or ""),
                    "what_happened": str(r["what_happened"] or ""),
                    "occurred_at": str(r["occurred_at"] or ""),
                    "symbol": str(r["symbol"] or ""),
                    "category": str(r["category"] or ""),
                }
            )
        return out
    finally:
        conn.close()


def sync_book_system_alerts(
    rows: Sequence[Any], *, account_equity: float = 25_000.0, cooldown_hours: int = 24
) -> int:
    """纪律/结构类提醒写入 category=book_system（与 typed 几何的 price_zone 并列）。"""
    if not rows:
        return 0
    now = _utc_ts()
    occurred_at = now.strftime("%Y-%m-%d %H:%M:%S")
    cb = _cooldown_bucket(cooldown_hours)
    inserted = 0
    conn = get_conn(read_only=False)
    try:
        for row in rows:
            sym = str(getattr(row, "symbol", "") or "").strip().upper()
            if not sym:
                continue
            try:
                px = float(getattr(row, "price", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if _skip_symbol(sym, px):
                continue
            view = build_book_card_view(row, account_equity=account_equity)
            zones = getattr(row, "zones", None)
            rec_lo = float(getattr(zones, "recommended_low", 0.0) or 0.0) if zones else 0.0
            rec_hi = float(getattr(zones, "recommended_high", 0.0) or 0.0) if zones else 0.0

            for tag in view.book_alert_tags:
                dedupe = f"book:{tag}:{sym}:{cb}"
                if tag == "ENTER_BUY_ZONE":
                    title = f"[ENTER_BUY_ZONE] {sym} 系统级可分批条件齐备"
                    what = (
                        f"{sym} 现价 ${px:,.2f}，typed 带约 ${rec_lo:,.2f}-${rec_hi:,.2f}；"
                        f"趋势模板/大盘/量价/风险收益比同时满足简化门槛。"
                    )
                    why = "Minervini + Murphy + Elder 合成：不是「便宜」，而是「计划内买点」窗口。"
                    relation = "仍须自行下单与仓位上限；默认只做第一笔，不梭哈。"
                    watch = "若随后放量跌破 MA50 或 R:R 恶化，系统买点假设作废。"
                    risk = "任何规则都可能连续亏损；亏损是系统成本的一部分。"
                    lvl = "major"
                elif tag == "NEAR_PIVOT":
                    title = f"[NEAR_PIVOT] {sym} 接近 pivot 代理"
                    what = f"{sym} 现价 ${px:,.2f}，接近近 20 日高 pivot 区域，等待放量突破或假突破风险。"
                    why = "Minervini：右侧买点常出现在 pivot 附近，而非远离结构的追高。"
                    relation = "观察是否出现放量突破与盈亏比；未确认前不加重仓。"
                    watch = "若突破失败回到区间内，回到「形态形成」阶段。"
                    risk = "假突破与来回打脸风险。"
                    lvl = "attention"
                elif tag == "BREAKOUT_WITH_VOLUME":
                    title = f"[BREAKOUT_WITH_VOLUME] {sym} 突破 pivot 且放量"
                    what = f"{sym} 现价 ${px:,.2f}，高于 pivot 代理且量能高于阈值。"
                    why = "Murphy：突破伴随成交量更可信；仍需 Elder 的止损与仓位。"
                    relation = "若 R:R 不足，纪律引擎会降级为观察；以引擎结论为准。"
                    watch = "关注次日持续性及大盘 regime。"
                    risk = "追高与回落风险；新闻事件可能瞬间改变结构。"
                    lvl = "major"
                elif tag == "EXTENDED_DONT_CHASE":
                    title = f"[EXTENDED_DONT_CHASE] {sym} 已明显延伸"
                    what = f"{sym} 现价 ${px:,.2f}，相对 pivot 过远；Douglas：警惕 FOMO，不在系统买点。"
                    why = "好股票也可能离好买点很远；延伸后风险收益比通常变差。"
                    relation = "若已持仓，偏向持有而非追价加仓；未持仓则等下一 setup。"
                    watch = "等待回踩、整理或新的 pivot。"
                    risk = "回撤可能快速吞噬浮盈。"
                    lvl = "attention"
                elif tag == "ENTER_EXTRA_DCA_ZONE":
                    ma200 = float(getattr(row, "ma200", 0.0) or 0.0)
                    if ma200 <= 0:
                        continue
                    ex_lo, ex_hi = ma200 * 0.90, ma200 * 1.03
                    title = f"[ENTER_EXTRA_DCA_ZONE] {sym} 进入 ETF 额外加仓带"
                    what = (
                        f"{sym} 现价 ${px:,.2f}，位于 MA200 附加仓带 ${ex_lo:,.2f}-${ex_hi:,.2f}；"
                        f"与每周定投独立，仅为「额外加仓」参考窗口。"
                    )
                    why = "价格回到长期均线附近的附加仓语境；不等于必须加仓。"
                    relation = "定投仍按计划；额外仓请结合现金流与总仓位上限。"
                    watch = "若跌穿带下沿或宏观转差，勿机械加码。"
                    risk = "ETF 仍可继续下跌；注意尾部风险。"
                    lvl = "major"
                else:
                    continue
                inserted += _insert(
                    conn,
                    category="book_system",
                    level=lvl,
                    symbol=sym,
                    title=title,
                    what=what,
                    why=why,
                    relation=relation,
                    watch=watch,
                    risk=risk,
                    dedupe=dedupe,
                    occurred_at=occurred_at,
                )
        conn.commit()
    finally:
        conn.close()
    return inserted


def sync_held_discipline_alerts(
    positions: Sequence[Any],
    quote_map: dict[str, Any],
    *,
    account_equity: float,
    cooldown_hours: int = 24,
) -> int:
    """已持仓：止损 / 减仓 语境写入 `book_system`（冷却去重）。"""
    if not positions:
        return 0
    from signals.signal_engine import evaluate_trade_discipline

    now = _utc_ts()
    occurred_at = now.strftime("%Y-%m-%d %H:%M:%S")
    cb = _cooldown_bucket(cooldown_hours)
    inserted = 0
    conn = get_conn(read_only=False)
    try:
        for p in positions:
            sym = str(getattr(p, "ticker", "") or "").strip().upper()
            if not sym:
                continue
            q = quote_map.get(sym)
            try:
                px = float(getattr(q, "px", 0.0) or 0.0) if q is not None else 0.0
            except (TypeError, ValueError):
                px = 0.0
            if _skip_symbol(sym, px):
                continue
            ac = float(getattr(p, "avg_cost_per_share", 0.0) or 0.0)
            if ac <= 0:
                continue
            sig = evaluate_trade_discipline(sym, avg_cost=ac, account_equity=account_equity)
            if sig.action == "STOP_TRIGGERED":
                dedupe = f"book:STOP_TRIGGERED:{sym}:{cb}"
                title = f"[STOP_TRIGGERED] {sym} 跌破计划止损参考"
                stp = float(sig.stop or 0.0)
                what = (
                    f"{sym} 现价 ${px:,.2f}，低于计划止损参考 ${stp:,.2f}（规则近似，非券商指令）。"
                    f" {sig.status_zh}"
                )
                why = "Douglas：按计划承认亏损是系统成本；若仅为摊平而改规则，已不属于原交易计划。"
                relation = "复核新闻/基本面后再决定是否减仓或离场；避免情绪补仓。"
                watch = "若价格快速收回止损之上，仍需区分「噪声」与「假设修复」。"
                risk = "摊平与死扛可能放大亏损；杠杆与集中度会放大尾部风险。"
                lvl = "major"
            elif sig.action == "REDUCE_OR_EXIT":
                dedupe = f"book:REDUCE_OR_EXIT:{sym}:{cb}"
                title = f"[REDUCE_OR_EXIT] {sym} 动能转弱信号"
                tail = "；".join(sig.reasons[-2:]) if sig.reasons else sig.status_zh
                what = f"{sym} 现价 ${px:,.2f}。{sig.status_zh} {tail}"
                why = "Murphy：跌破均线且放量常伴随结构走弱；不等于必须卖，但应进入纪律复核。"
                relation = "对照持仓权重、成本与组合健康度，决定是否减仓或收紧跟踪止损。"
                watch = "若随后收回均线且量能改善，可重新评估；避免来回打脸式频繁交易。"
                risk = "假信号与震荡市可能反复触发；注意交易成本。"
                lvl = "attention"
            else:
                continue
            inserted += _insert(
                conn,
                category="book_system",
                level=lvl,
                symbol=sym,
                title=title,
                what=what,
                why=why,
                relation=relation,
                watch=watch,
                risk=risk,
                dedupe=dedupe,
                occurred_at=occurred_at,
            )
        conn.commit()
    finally:
        conn.close()
    return inserted
