from __future__ import annotations

from collections import defaultdict
import importlib
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st
import yaml

from data_layer.market_data import fetch_quotes
from data_layer import portfolio_analytics as pa
from data_layer import guru_copybook
from db.client import get_conn
from monitors.watchlist_sentinels import add_sentinel, is_sentinel_starred, remove_sentinel
from stock_picker import entry_zone as _entry_zone
from stock_picker import quality_filter as qf
from stock_picker import zone_context as zone_ctx
from stock_picker.steady_universe import AVOID_LIBRARY, STEADY_LIBRARY
from tasks.buy_zone_alerts import fetch_recent_price_zone_alerts, sync_book_system_alerts, sync_price_zone_alerts
from tasks.stock_recommendations import generate_recommendation_list
from signals.market_regime import compute_market_regime
from signals.signal_engine import evaluate_trade_discipline
from ui.radar_widget import render_major_moves

if TYPE_CHECKING:
    from stock_picker.quality_filter import OpportunityRow


def _ensure_entry_zone_compat() -> None:
    if hasattr(_entry_zone, "calc_typed_entry_zones"):
        return

    def _compat_calc_typed_entry_zones(**kwargs):
        current_px = float(kwargs.get("current_px") or 0.0)
        low_3m = float(kwargs.get("low_3m") or current_px)
        low_6m = float(kwargs.get("low_6m") or low_3m)
        ma50 = float(kwargs.get("ma50") or current_px)
        ma200 = float(kwargs.get("ma200") or ma50)
        zones = _entry_zone.calc_entry_zones(
            current_px=current_px,
            low_3m=low_3m,
            low_6m=low_6m,
            ma50=ma50,
            ma200=ma200,
        )
        if not hasattr(zones, "stock_type"):
            setattr(zones, "stock_type", str(kwargs.get("stock_type") or "steady_growth"))
        if not hasattr(zones, "trend_warning"):
            setattr(zones, "trend_warning", bool(ma200 and current_px < ma200 * 0.95))
        if not hasattr(zones, "status"):
            in_zone = zones.recommended_low <= current_px <= zones.recommended_high
            setattr(zones, "status", "当前可分批" if in_zone else "等回推荐区间")
        return zones

    setattr(_entry_zone, "calc_typed_entry_zones", _compat_calc_typed_entry_zones)


def _guru_mod():
    """Return latest guru_copybook module with hot-reload tolerance."""
    global guru_copybook
    try:
        guru_copybook = importlib.reload(guru_copybook)
    except Exception:
        if not hasattr(guru_copybook, "build_guru_symbol_meta"):
            raise
    return guru_copybook


def _build_guru_symbol_meta_safe() -> dict:
    mod = _guru_mod()
    fn = getattr(mod, "build_guru_symbol_meta", None)
    if callable(fn):
        try:
            return fn() or {}
        except Exception:
            return {}
    return {}


def _load_guru_watchlist_safe() -> dict:
    mod = _guru_mod()
    fn = getattr(mod, "load_guru_watchlist", None)
    if callable(fn):
        try:
            return fn() or {}
        except Exception:
            return {}
    return {}


def _guru_evidence_label_safe(meta, limit: int = 2) -> str:
    mod = _guru_mod()
    fn = getattr(mod, "guru_evidence_label", None)
    if callable(fn):
        try:
            return str(fn(meta, limit) or "")
        except Exception:
            pass
    evidence = list(getattr(meta, "guru_evidence", []) or [])
    labels: list[str] = []
    for e in evidence[:limit]:
        manager = str(e.get("manager") or "").strip()
        person = str(e.get("person") or "").strip()
        who = person or manager or "作业池"
        pct = e.get("weight_pct")
        try:
            pct_text = f"{float(pct):.2f}".rstrip("0").rstrip(".")
            labels.append(f"{who} {pct_text}%")
        except Exception:
            labels.append(who)
    return " / ".join([x for x in labels if x])


def _quality_filter_module():
    """Return the latest quality_filter module.

    Streamlit can keep an already-imported module object alive across code edits.
    Reloading here avoids crashing when the page sees an older module shape.
    """

    global qf
    _ensure_entry_zone_compat()
    try:
        qf = importlib.reload(qf)
    except Exception:
        # Hard fallback: stale module objects can survive Streamlit hot-reload.
        qf = importlib.import_module("stock_picker.quality_filter")
        qf = importlib.reload(qf)
    return qf


def _legacy_opportunity_rows() -> list[SimpleNamespace]:
    mod = _quality_filter_module()
    if not hasattr(mod, "build_stable_picks"):
        return []
    rows = []
    try:
        picks = mod.build_stable_picks()
    except AttributeError:
        # Recover from stale `entry_zone` objects inside quality_filter.
        try:
            import stock_picker.entry_zone as _entry_zone  # noqa: PLC0415

            importlib.reload(_entry_zone)
            _ensure_entry_zone_compat()
            mod = importlib.reload(mod)
            picks = mod.build_stable_picks()
        except Exception:
            return []
    for p in picks:
        rows.append(
            SimpleNamespace(
                symbol=p.symbol,
                name=p.symbol,
                sector=p.sector,
                bucket=p.sector,
                source="legacy",
                price=p.current_px,
                chg_pct=p.chg_pct,
                ret_1w=0.0,
                ret_1m=0.0,
                ret_3m=0.0,
                q_score=max(1, min(10, p.stars * 2)),
                t_score=max(1, min(10, p.stars * 2)),
                p_score=6,
                p_status="合理区间",
                c_score=0,
                label="观察候选",
                advice="可分批" if p.stars >= 4 else "小仓观察",
                priority_score=float(p.stars),
                alert_suggestions=[
                    f"跌入推荐区间 ${p.zones.recommended_low:,.2f}-${p.zones.recommended_high:,.2f}",
                    f"跌到观察位 ${p.zones.deep_value:,.2f}",
                ],
                zones=p.zones,
                exits=p.exits,
                reason_short=p.reason,
                reason_detail=p.reason,
                risk_short=p.risk,
                hover_explanation=f"{p.reason}\n\n风险：{p.risk}",
                bucket_gap_note="旧版候选池兼容显示",
                news_score=0,
                news_evidence=[],
                decision_queue="小仓观察",
                is_buyable_now=False,
                buyable_tier="",
                buyable_reason="",
                buy_mode="wait_pullback",
                current_action="observe",
                zone_position=1.0,
                ma50=0.0,
                ma200=0.0,
                high_52w=0.0,
            )
        )
    return rows


def _load_opportunity_rows() -> list:
    mod = _quality_filter_module()
    fn = getattr(mod, "build_opportunity_rows", None)
    if callable(fn):
        try:
            return fn()
        except AttributeError:
            # Backward compatibility for stale module objects during hot-reload.
            return _legacy_opportunity_rows()
    return _legacy_opportunity_rows()


def _portfolio_weights(rows: list) -> dict[str, float]:
    mod = _quality_filter_module()
    fn = getattr(mod, "portfolio_bucket_weights", None)
    if callable(fn):
        return fn(rows)
    return {}


def _bucket_descs() -> dict[str, str]:
    mod = _quality_filter_module()
    fn = getattr(mod, "bucket_descriptions", None)
    if callable(fn):
        return fn()
    return {}


def _pct(v: float) -> str:
    return f"{v:+.1f}%"


def _badge_color(text: str) -> str:
    if text in {"可分批", "可重点关注", "只适合定投"}:
        return "green"
    if text in {"等回踩", "小仓观察"}:
        return "blue"
    if text in {"偏高不追", "暂不看"}:
        return "red"
    return "gray"


def _starred(symbol: str) -> bool:
    conn = get_conn(read_only=False)
    try:
        return is_sentinel_starred(conn, symbol)
    finally:
        conn.close()


def _star_button(symbol: str, *, key: str, hint: str = "") -> None:
    sym = symbol.upper()
    on = _starred(sym)
    label = "⭐" if on else "☆"
    if st.button(label, key=f"{key}_star_toggle"):
        conn = get_conn(read_only=False)
        try:
            changed = remove_sentinel(conn, sym) if on else add_sentinel(conn, sym)
        finally:
            conn.close()
        if changed and not on:
            st.success(f"{sym} 已收藏：自动提醒买入区/回踩/财报/大跌。")
        elif changed and on:
            st.info(f"{sym} 已取消收藏。")
        st.rerun()
    if on:
        st.caption(
            "⭐ 已收藏：默认启用「跌入基础分批区 / 回踩均线 / 大跌」等哨兵；"
            "右侧「智能提醒中心」汇总库内 `price_zone` 与 `book_system`（几何区间 + 书籍纪律类），按冷却窗去重。"
        )
    else:
        st.caption(
            "☆ 点星后：启用默认哨兵；打开本页时会尝试写入 `price_zone` / `book_system` alerts（与点星独立，见侧栏）。"
        )
    if hint:
        st.caption(f"提示：{hint}")


def _render_buy_side(label: str, price: float, current_price: float) -> str | None:
    px = float(current_price or 0.0)
    p = float(price or 0.0)
    if px <= 0 or p <= 0 or p >= px:
        return None
    pct = (p / px - 1) * 100.0
    return f"{label}: ${p:,.2f}（{pct:+.1f}%）"


def _render_high_side(label: str, price: float, current_price: float) -> str | None:
    px = float(current_price or 0.0)
    p = float(price or 0.0)
    if px <= 0 or p <= px:
        return None
    pct = (p / px - 1) * 100.0
    return f"{label}: ${p:,.2f}+（{pct:+.1f}%）"


def _render_simple_watchlist_sections(rows: list[OpportunityRow], held_map: dict[str, dict]) -> None:
    st.markdown("### 🛡️ 稳健票库")
    st.caption("按“长期向上、回撤可控、不是过山车”的思路整理；价格、动作、区间统一读取下方同一套 DecisionCard。")
    by_symbol = {str(r.symbol).upper(): r for r in rows}
    grouped: dict[str, list[OpportunityRow]] = defaultdict(list)
    missing: list[tuple[str, dict]] = []
    for sym, meta in STEADY_LIBRARY.items():
        row = by_symbol.get(sym.upper())
        if row:
            grouped[str(meta.get("category") or row.bucket)].append(row)
        else:
            missing.append((sym, meta))
    for category in ["防御之王", "必需消费", "金融 / 支付", "医疗健康", "稳健成长", "ETF 推荐"]:
        sub = grouped.get(category, [])
        if not sub:
            continue
        st.markdown(f"#### {category}")
        cols = st.columns(2)
        for i, row in enumerate(sub[:6]):
            with cols[i % 2]:
                _render_card(row, held_map.get(row.symbol, {"is_held": False}), key_prefix=f"steady_{category}_{i}")

    with st.expander("❌ 不要碰 / 不放进稳健票库", expanded=False):
        for sym, reason in AVOID_LIBRARY.items():
            st.write(f"- `{sym}`：{reason}")
        if missing:
            st.caption("以下稳健库符号暂未进入统一候选池，因此不显示价格/动作，避免两套逻辑冲突：")
            st.write(" / ".join([sym for sym, _ in missing]))


def _render_summary(rows: list[OpportunityRow], weights: dict[str, float]) -> None:
    tech = weights.get("科技 / AI / 半导体", 0.0) + weights.get("Communication Services", 0.0)
    low_buckets = [
        b
        for b in ("消费防御", "医疗健康", "金融 / 支付", "工业 / 基建 / 电力", "能源 / 公用事业")
        if weights.get(b, 0.0) < 8
    ]
    ready = [r.symbol for r in rows if str(getattr(r, "current_action", "")) in {"priority_buyable", "buyable"}][:5]
    wait = [
        r.symbol
        for r in rows
        if str(getattr(r, "current_action", "")) in {"wait_pullback", "dca_only", "momentum_alert_only"}
    ][:5]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("仓位偏科", f"科技约 {tech:.0f}%", "偏高" if tech >= 45 else "正常")
    c1.caption(("低配：" + " / ".join(low_buckets[:3])) if low_buckets else "板块分布暂时可接受")
    c2.metric("现在可分批", str(len(ready)))
    c2.caption(" / ".join(ready) if ready else "暂无直接可分批候选")
    c3.metric("等回踩", str(len(wait)))
    c3.caption(" / ".join(wait) if wait else "暂无明显过热候选")
    c4.metric("提醒建议", str(sum(len(r.alert_suggestions) for r in rows[:12])))
    c4.caption("每张卡片内可展开提醒 preset")


def _render_gap_panel(weights: dict[str, float]) -> None:
    st.markdown("#### 行业补仓建议")
    buckets = [
        "ETF / 大盘底仓",
        "消费防御",
        "医疗健康",
        "金融 / 支付",
        "工业 / 基建 / 电力",
        "能源 / 公用事业",
        "通信服务",
        "科技 / AI / 半导体",
        "高波动观察",
    ]
    for b in buckets:
        w = weights.get(b, 0.0)
        if b == "科技 / AI / 半导体" and w >= 45:
            st.error(f"{b}: {w:.0f}% · 超配 · 暂停加仓")
        elif b in {"消费防御", "医疗健康", "金融 / 支付", "能源 / 公用事业"} and w < 8:
            st.warning(f"{b}: {w:.0f}% · 低配 · 建议补一点")
        elif b == "ETF / 大盘底仓" and w < 20:
            st.info(f"{b}: {w:.0f}% · 可定投补底仓")
        else:
            st.caption(f"{b}: {w:.0f}% · 正常观察")


_ACTION_RANK = {
    "可重点关注": 5,
    "当前可分批": 4,
    "可分批": 4,
    "只适合定投": 3,
    "小仓观察": 2,
    "等回踩": 1,
    "偏高不追": 0,
    "暂不看": -1,
}

_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _plain_notes_map() -> dict[str, tuple[str, str]]:
    p = _ROOT / "config" / "plain_notes.yaml"
    if not p.exists():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    notes = raw.get("notes") or {}
    out: dict[str, tuple[str, str]] = {}
    for sym, body in notes.items():
        s = str(sym).upper().strip()
        if not s:
            continue
        d = body or {}
        out[s] = (
            str(d.get("plain_english_note") or "").strip(),
            str(d.get("risk_note") or "").strip(),
        )
    return out


def _bucket_gap_bonus(bucket: str, weights: dict[str, float]) -> float:
    w = float(weights.get(bucket, 0.0))
    if bucket == "科技 / AI / 半导体" and w >= 45:
        return -2.0
    if bucket in {"消费防御", "医疗健康", "金融 / 支付", "工业 / 基建 / 电力", "能源 / 公用事业"} and w < 8:
        return 2.0
    if bucket == "ETF / 大盘底仓" and w < 20:
        return 1.0
    return 0.0


@lru_cache(maxsize=512)
def _history_brief(symbol: str) -> dict[str, float]:
    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception:
        return {"ret_5y": 0.0, "ret_1y": 0.0, "ret_3m": 0.0, "max_dd": 0.0, "vol": 0.0}
    try:
        h = yf.Ticker(symbol).history(period="5y", interval="1d", auto_adjust=True)
    except Exception:
        return {"ret_5y": 0.0, "ret_1y": 0.0, "ret_3m": 0.0, "max_dd": 0.0, "vol": 0.0}
    if h is None or h.empty:
        return {"ret_5y": 0.0, "ret_1y": 0.0, "ret_3m": 0.0, "max_dd": 0.0, "vol": 0.0}
    c = h["Close"].astype(float).dropna()
    if c.empty:
        return {"ret_5y": 0.0, "ret_1y": 0.0, "ret_3m": 0.0, "max_dd": 0.0, "vol": 0.0}
    last = float(c.iloc[-1])

    def _ret(days: int) -> float:
        if len(c) <= days:
            return 0.0
        old = float(c.iloc[-days - 1])
        return ((last - old) / old * 100.0) if old else 0.0

    peak = c.cummax()
    dd = ((c - peak) / peak * 100.0).min() if not peak.empty else 0.0
    vol = c.pct_change().dropna().tail(63).std() * 100.0 if len(c) > 20 else 0.0
    return {
        "ret_5y": _ret(252 * 5) if len(c) > 252 * 2 else _ret(len(c) - 1),
        "ret_1y": _ret(252),
        "ret_3m": _ret(63),
        "max_dd": float(dd),
        "vol": float(vol),
    }


def _vol_label(vol: float) -> str:
    if vol >= 4.5:
        return "高"
    if vol >= 2.5:
        return "中"
    return "低"


def _fmt_hist_pct(label: str, value: float) -> str:
    v = float(value or 0.0)
    if abs(v) < 0.05:
        return f"{label} 数据不足"
    return f"{label} {v:+.1f}%"


def _compute_display_rank(row: OpportunityRow, weights: dict[str, float]) -> float:
    action = str(getattr(row, "current_action", "") or "")
    action_rank = {
        "priority_buyable": 5,
        "buyable": 4,
        "dca_only": 3,
        "cautious_buyable": 2,
        "observe": 2,
        "wait_pullback": 1,
        "momentum_alert_only": 0,
        "avoid_chase": 0,
        "do_not_touch": -1,
    }.get(action, _ACTION_RANK.get(str(row.advice), 0))
    p_status = str(getattr(row, "p_status", ""))
    bucket = str(getattr(row, "bucket", ""))
    overheat_penalty = 1.0 if p_status == "过热" else 0.5 if p_status == "偏高" else 0.0
    overweight_penalty = 1.0 if bucket == "科技 / AI / 半导体" and float(weights.get(bucket, 0.0)) >= 45 else 0.0
    return (
        (1000 if action in {"priority_buyable", "buyable"} else 0)
        + action_rank * 100
        + _bucket_gap_bonus(bucket, weights) * 20
        + float(row.q_score) * 5
        + float(row.t_score) * 3
        + float(row.c_score) * 4
        + float(getattr(row, "guru_score", 0)) * 2
        + float(getattr(row, "news_score", 0)) * 2
        - overheat_penalty * 30
        - overweight_penalty * 15
    )


def _holding_action(row: OpportunityRow, held: dict) -> str:
    if not held.get("is_held"):
        return "未持有"
    ur = float(held.get("unrealized_return_pct") or 0.0)
    w = float(held.get("position_weight") or 0.0)
    avg_cost = float(held.get("avg_cost") or 0.0)
    ma50 = float(getattr(row, "ma50", 0.0) or 0.0)
    if w >= 15 or (str(row.bucket) == "高波动观察" and w >= 8):
        return "减仓评估"
    if ur >= 25:
        return "保护利润"
    if ur <= -5 or (avg_cost > 0 and float(row.price) < avg_cost) or (ma50 > 0 and float(row.price) < ma50):
        return "检查逻辑"
    return "继续持有"


def _buyable_badge(row: OpportunityRow, bc: Any | None = None) -> str:
    action = str(getattr(row, "current_action", "") or "")
    if action == "cautious_buyable" and bc is not None:
        if (not getattr(bc, "minervini_template_ok", True)) or int(getattr(row, "t_score", 0) or 0) < 4:
            return "⚠️ 观察区间，不是买入信号"
    if action == "priority_buyable":
        return "✅ 优先可分批"
    if action == "buyable":
        return "🟢 可分批"
    if action == "cautious_buyable":
        return "🟡 谨慎小仓"
    if action in {"wait_pullback", "dca_only"}:
        return "🔔 等回踩"
    if action in {"avoid_chase", "momentum_alert_only"} or str(getattr(row, "advice", "")) == "偏高不追":
        return "🔴 偏高不追"
    return ""


def _position_map(rows: list[OpportunityRow]) -> dict[str, dict]:
    pos = pa.load_positions()
    by_sym = {p.ticker.upper(): p for p in pos}
    syms = sorted({r.symbol for r in rows if r.symbol in by_sym})
    qmap = {q.symbol.upper(): q for q in fetch_quotes(syms)} if syms else {}
    total_mv = 0.0
    mv_by: dict[str, float] = {}
    for sym, p in by_sym.items():
        q = qmap.get(sym)
        px = float(q.px) if q else float(p.avg_cost_per_share)
        mv = float(p.qty) * px
        mv_by[sym] = mv
        total_mv += mv
    out: dict[str, dict] = {}
    for sym, p in by_sym.items():
        q = qmap.get(sym)
        px = float(q.px) if q else float(p.avg_cost_per_share)
        mv = mv_by.get(sym, 0.0)
        cost = float(p.qty) * float(p.avg_cost_per_share)
        ur = ((mv - cost) / cost * 100.0) if cost > 0 else 0.0
        out[sym] = {
            "is_held": True,
            "shares": float(p.qty),
            "avg_cost": float(p.avg_cost_per_share),
            "position_weight": (mv / total_mv * 100.0) if total_mv > 0 else 0.0,
            "unrealized_return_pct": ur,
            "unrealized_pnl": mv - cost,
        }
    return out


def _render_card(row: OpportunityRow, held: dict | None = None, *, key_prefix: str = "default") -> None:
    held = held or {"is_held": False}
    hist = _history_brief(str(row.symbol))
    eq_card = float(st.session_state.get("signal_engine_equity_picker", 25000.0))
    try:
        from signals.book_card_context import build_book_card_view

        bc = build_book_card_view(row, account_equity=eq_card)
    except Exception:
        bc = None
    note, risk_note = _plain_notes_map().get(
        str(row.symbol).upper(),
        (str(getattr(row, "reason_short", "") or "行业里相对稳健的候选。"), str(getattr(row, "risk_short", "") or "注意仓位与回撤。")),
    )
    with st.container(border=True):
        badge = _buyable_badge(row, bc)
        title = f"**`{row.symbol}` · {row.name}**"
        st.markdown(f"{title}  \n{badge}" if badge else title)
        st.caption(f"{row.bucket} · {row.label}")
        st.markdown(
            f"价格 **${row.price:,.2f}**（{row.chg_pct:+.2f}%） · "
            f":{_badge_color(row.advice)}[建议：{row.advice}] · 优先级 {row.priority_score:.1f}"
        )
        st.caption(
            f"{_fmt_hist_pct('5Y', hist['ret_5y'])} · {_fmt_hist_pct('1Y', hist['ret_1y'])} · {_fmt_hist_pct('3M', hist['ret_3m'])}"
        )
        st.caption(
            f"最大回撤 {hist['max_dd']:.1f}% · 波动 {_vol_label(hist['vol'])} · "
            f"Q {row.q_score}/10 · T {row.t_score}/10 · P {row.p_status} · C {row.c_score}/10"
        )
        if getattr(row, "decision_queue", ""):
            st.caption(f"队列：{getattr(row, 'decision_queue')}")
        if str(getattr(row, "current_action", "")) in {"priority_buyable", "buyable"} and getattr(row, "buyable_reason", ""):
            if bc and bc.in_zone and not bc.system_buyable:
                st.warning(
                    "**观察区间，不是系统买点**：页面标为可分批，但书籍合成门未通过。\n"
                    + "\n".join(f"- {b}" for b in (bc.system_buy_blockers or [])[:6])
                )
            else:
                st.success(str(getattr(row, "buyable_reason", "")))
                if bc and bc.system_buyable and bc.in_zone:
                    st.caption(
                        "系统级可分批：**通过**（typed 带内 + Minervini 模板 + 大盘非 risk-off + 无放量破位 + R:R 达门槛）。"
                        " 只做计划内第一笔，并接受计划止损。"
                    )
        elif str(getattr(row, "current_action", "")) == "cautious_buyable" and getattr(row, "buyable_reason", ""):
            st.warning(str(getattr(row, "buyable_reason", "")))
            if bc and (bc.system_buy_blockers or []):
                st.caption("系统未通过要点：" + "；".join(bc.system_buy_blockers[:5]))
        if int(getattr(row, "guru_score", 0)) != 0:
            evidence = list(getattr(row, "guru_evidence", []) or [])
            if evidence:
                tags = []
                for e in evidence[:2]:
                    person = str(e.get("person") or "").strip()
                    manager = str(e.get("manager") or "").strip()
                    pct = e.get("weight_pct")
                    pct_txt = ""
                    try:
                        pct_val = f"{float(pct):.2f}".rstrip("0").rstrip(".")
                        pct_txt = f" {pct_val}%"
                    except Exception:
                        pct_txt = ""
                    tags.append(f"{person or manager}{pct_txt}")
                st.caption(f"G {int(getattr(row, 'guru_score', 0)):+d} · " + " / ".join(tags))
            else:
                src = list(getattr(row, "guru_sources", []) or [])
                st.caption(f"G {int(getattr(row, 'guru_score', 0)):+d} · 参考：{' / '.join(src[:2]) if src else '作业池'}")
        action = str(getattr(row, "current_action", "") or "")
        buy_mode = str(getattr(row, "buy_mode", "") or "")
        if action in {"priority_buyable", "buyable"}:
            zone_label = "当前进入可分批区" if (bc and bc.system_buyable and bc.in_zone) else "当前可分批（页面标签）"
        elif action == "cautious_buyable":
            zone_label = (
                "观察区间（不是买入信号）"
                if (bc and (not bc.minervini_template_ok or int(row.t_score) < 4))
                else "区间内但需谨慎"
            )
        elif action == "momentum_alert_only":
            zone_label = "重大异动，不追"
        elif buy_mode == "dca_only" or action == "dca_only":
            zone_label = "长期定投可继续，不是优先加仓点"
        elif buy_mode == "deep_add":
            zone_label = "深度回调区"
        elif action == "wait_pullback":
            zone_label = "基础分批区（当前动作：等回踩）"
        else:
            zone_label = "基础合理区间"
        kind = zone_ctx.instrument_kind(str(row.symbol), str(row.bucket), str(getattr(row, "stock_type", "")))
        ma200 = float(getattr(row, "ma200", 0.0) or 0.0)
        if kind == "etf" and ma200 > 0:
            etf_blk = zone_ctx.format_etf_dca_block(str(row.symbol), float(row.price), ma200)
            if etf_blk:
                st.markdown(etf_blk)
            st.caption(
                "下方 typed 区间来自通用入口模型，可作横向对比；**执行以蓝色块：定投 + MA200 附加仓带为准**。"
            )
        typed_band = zone_ctx.zone_label_for_trend(row)
        st.write(f"{zone_label} — {typed_band}：${row.zones.recommended_low:,.2f} - ${row.zones.recommended_high:,.2f}")
        if bc and str(kind) != "etf":
            st.caption(f"**依据**：{bc.zone_rationale_zh}")
            st.caption(f"**失效 / 风险**：{bc.invalidation_zh}")
            if bc.system_buyable and bc.in_zone:
                st.caption(f"**建议**：{bc.first_tranche_hint} 只做第一笔，不一次性买满。")
            elif bc.in_zone:
                st.caption(
                    "**动作**：等待 Trend Template / 相对强度 / R:R 修复；"
                    "若仅观察，可用右侧提醒与「书籍系统检查」跟踪。"
                )
        st.caption(
            f"更舒服回踩 ${row.zones.comfortable_buy:,.2f} · "
            f"深度回调 ${row.zones.deep_value:,.2f} · 不追位 ${row.zones.avoid_above:,.2f}+"
        )
        px = float(row.price)
        sc, conf_zh, conf_det = zone_ctx.zone_confidence(
            symbol=str(row.symbol),
            row=row,
            hist=hist,
            rec_lo=float(row.zones.recommended_low),
            rec_hi=float(row.zones.recommended_high),
            px=px,
        )
        st.caption(zone_ctx.zone_basis_line(row))
        st.caption(f"区间可信度：{conf_zh}（{sc}/10）· {conf_det}")
        zw = zone_ctx.zone_width_pct(float(row.zones.recommended_low), float(row.zones.recommended_high), px)
        if kind != "etf" and zw > 0.35:
            st.warning("区间相对现价过宽：仅供观察，等待均线/平台收敛后再动作。")
        elif kind == "etf" and ma200 > 0:
            b = zone_ctx.etf_extra_band(ma200)
            ew = (b["extra_high"] - b["extra_low"]) / max(px, 0.01)
            if ew > 0.35:
                st.caption("额外加仓带相对较宽：多为长期均线带宽所致，请结合大盘。")
        if bc:
            if bc.pivot is not None:
                cap = (
                    f"**技术摘要**：纪律引擎 `{bc.discipline_action}`（{bc.discipline_action_zh}）· "
                    f"大盘 `{bc.regime_label}` · Pivot 代理 ${bc.pivot:,.2f}"
                )
            else:
                cap = f"**技术摘要**：{bc.discipline_action_zh} · 大盘 `{bc.regime_label}`"
            st.caption(cap)
            with st.expander("书籍系统检查（Murphy · Minervini · VCP · Douglas · Elder）", expanded=False):
                st.markdown(f"**1. 环境（Elder / 大盘）**  \n`{bc.regime_label}` · {bc.regime_zh}")
                st.markdown("**2. Murphy（趋势 / 支撑压力 / 量能）**")
                for line in bc.murphy_lines:
                    st.markdown(f"- {line}")
                st.markdown("**3. Minervini（强势股模板 + 相对强度）**")
                st.markdown("- " + ("Trend Template：**通过**" if bc.minervini_template_ok else "Trend Template：**未通过**（默认不给系统级买点）。"))
                for line in bc.minervini_lines:
                    st.markdown(f"- {line}")
                st.markdown("**4. VCP-like（波动收缩启发式）**")
                for line in bc.vcp_detail_lines:
                    st.markdown(f"- {line}")
                for line in bc.vcp_lines:
                    st.markdown(f"- {line}")
                if bc.vcp_label and not bc.vcp_detail_lines:
                    st.caption(bc.vcp_label)
                st.markdown("**5. Douglas（心理纪律）**")
                for line in bc.douglas_lines:
                    st.markdown(f"- {line}")
                st.markdown("**6. Elder（Money：止损 / 仓位 / 风险）**")
                for line in bc.elder_lines:
                    st.markdown(f"- {line}")
                if bc.system_buyable:
                    st.success("系统级买点门：**通过**（仍非自动下单）。")
                elif bc.in_zone:
                    st.warning("系统在 typed 带内，但**买点门未通过**：" + "；".join(bc.system_buy_blockers[:6]))
                else:
                    st.info("现价不在 typed 主带内：以「等回踩 / 结构」为主，买点门单独计算。")
        st.caption(f"为什么看它：{note}")
        st.caption(f"风险：{risk_note}")
        if held.get("is_held"):
            st.caption(
                f"持仓：{held.get('shares', 0):.4f} 股 · 成本 ${held.get('avg_cost', 0):,.2f} · "
                f"浮盈 {held.get('unrealized_return_pct', 0):+.2f}% · 仓位 {held.get('position_weight', 0):.1f}%"
            )
            st.markdown(f"**持仓建议：{_holding_action(row, held)}**")
            c1, c2 = st.columns(2)
            with c1.popover("持仓提醒"):
                st.write("- 从高点回撤 8% / 12% / 15%")
                st.write("- 跌回成本价")
                st.write("- 跌破 50/200 日线")
                st.write("- 单只仓位超过 15% / 20%")
            with c2.popover("补仓提醒"):
                st.write("- 跌入推荐区间")
                st.write("- 跌到观察位")
                st.write("- 单日大跌但趋势未坏")
        else:
            if row.advice in {"可分批", "可重点关注"}:
                btn = "加入今日可买"
            elif row.advice in {"等回踩", "偏高不追"}:
                btn = "设回踩提醒"
            else:
                btn = "加入观察"
            c1, c2 = st.columns(2)
            c1.button(btn, key=f"{key_prefix}_buy_action_{row.symbol}_{row.bucket}")
            with c2.popover("提醒预设"):
                st.write("- 跌入推荐区间提醒")
                st.write("- 到达观察位提醒")
                st.write("- 从近期高点回撤 8%")
                st.write("- 接近不追位提醒")
                st.write("- 单日下跌 3% / 5%")
            _star_button(row.symbol, key=f"{key_prefix}_sentinel_{row.symbol}", hint=note)

        with st.expander("为什么 / 提醒", expanded=False):
            st.write(row.hover_explanation)
            st.caption(f"风险：{row.risk_short}")
            evidence = list(getattr(row, "news_evidence", None) or [])
            if evidence:
                st.markdown("**新闻证据**")
                for item in evidence[:3]:
                    text = item.get("one_line_zh") or item.get("title") or ""
                    url = item.get("url") or ""
                    source = item.get("source") or ""
                    when = str(item.get("published_at") or "")[:16].replace("T", " ")
                    if url:
                        st.write(f"- [{text}]({url}) · {source} · {when}")
                    else:
                        st.write(f"- {text} · {source} · {when}")
            st.markdown("**建议提醒**")
            for item in row.alert_suggestions:
                st.write(f"- {item}")


def _render_bucket_section(bucket: str, rows: list[OpportunityRow], desc: str) -> None:
    st.markdown(f"### {bucket}")
    if desc:
        st.caption(desc)
    cols = st.columns(2)
    for i, row in enumerate(rows[:6]):
        with cols[i % 2]:
            _render_card(row, key_prefix=f"bucket_{bucket}_{i}")


def _push_guru_zone_alerts(candidates: list[OpportunityRow], weights: dict[str, float]) -> int:
    if not candidates:
        return 0
    utc_now = datetime.now(timezone.utc)
    today_key = utc_now.strftime("%Y%m%d")
    inserted = 0
    conn = get_conn(read_only=False)
    try:
        for row in candidates:
            sym = str(row.symbol).upper()
            dedupe = f"guru-zone:{today_key}:{sym}"
            low_alloc = _bucket_gap_bonus(str(row.bucket), weights) > 0
            title = f"[大佬作业池] {sym} 进入推荐区间"
            what = f"{sym} 当前 ${float(row.price):,.2f}，推荐区间 ${float(row.zones.recommended_low):,.2f}-${float(row.zones.recommended_high):,.2f}。"
            why = "大佬作业池候选 + 你当前规则允许分批关注。"
            if low_alloc:
                why += " 该板块在你组合中相对低配，优先级提升。"
            risk = "13F/作业池仅作灵感，不是直接买入信号；注意仓位控制。"
            cur = conn.execute(
                """INSERT OR IGNORE INTO alerts
                   (level, category, symbol, title, what_happened, why_matters, risk, dedupe_key, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("attention", "opportunity", sym, title, what, why, risk, dedupe, utc_now.strftime("%Y-%m-%d %H:%M:%S")),
            )
            if int(cur.rowcount or 0) > 0:
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def render() -> None:
    st.subheader("按板块找靠谱机会")
    st.caption(
        "帮你在科技股之外分散持仓，快速查看各行业中长期趋势向上、相对靠谱、适合关注的标的。"
    )
    render_major_moves(limit=4, compact=True)

    with st.expander("这个页面怎么看", expanded=False):
        st.markdown(
            "靠谱 = 公司/资产本身质量较高 + 长期趋势不是向下 + 业务有真实需求 + 不完全靠短期炒作。\n\n"
            "评分拆成 Q/T/P/C：Q=质量，T=趋势，P=位置，C=催化。页面不是喊你立刻买，"
            "而是告诉你它在组合里扮演什么角色，以及现在更适合分批、等回踩还是只设提醒。"
        )

    rows = _load_opportunity_rows()
    if not rows:
        st.info("暂无候选（可能行情/数据源暂不可用）。")
        return

    guru_meta = _build_guru_symbol_meta_safe()
    for r in rows:
        gm = guru_meta.get(str(getattr(r, "symbol", "")).upper())
        setattr(r, "guru_score", int(gm.guru_score) if gm else 0)
        setattr(r, "guru_sources", list(gm.guru_sources) if gm else [])
        setattr(r, "guru_evidence", list(gm.guru_evidence) if gm else [])
        setattr(r, "guru_theme", str(gm.sector_theme) if gm else "")

    weights = _portfolio_weights(rows)
    held_map = _position_map(rows)
    ranked_rows = sorted(rows, key=lambda r: _compute_display_rank(r, weights), reverse=True)

    try:
        eq_sync = float(st.session_state.get("signal_engine_equity_picker", 25000.0))
    except Exception:
        eq_sync = 25000.0
    try:
        sync_price_zone_alerts(ranked_rows, cooldown_hours=24, account_equity=eq_sync)
        sync_book_system_alerts(ranked_rows, account_equity=eq_sync, cooldown_hours=24)
    except Exception:
        # 数据源或本地 DB 短暂不可用时，不阻塞整页渲染。
        pass

    with st.expander("📐 中短期纪律引擎（未持仓视角 · 规则预览）", expanded=False):
        st.caption(
            "对排序靠前的候选跑一遍：**大盘 → 趋势模板 → pivot/量能 → 风险收益**。"
            "用于回答「能不能追 / 该不该等」，**不是**价格预测，也不会下单。"
        )
        regime = compute_market_regime()
        st.markdown(f"**大盘**：{regime.zh}（`{regime.label}`）")
        st.caption(regime.detail)
        eq = st.number_input(
            "假设账户总资产（美元，用于 1% 风险建议股数）",
            min_value=1000.0,
            max_value=50_000_000.0,
            value=25_000.0,
            step=1000.0,
            key="signal_engine_equity_picker",
        )
        preview: list[dict] = []
        for r in ranked_rows[:14]:
            sym = str(getattr(r, "symbol", "")).upper()
            if not sym or sym in held_map:
                continue
            sig = evaluate_trade_discipline(sym, avg_cost=None, account_equity=float(eq))
            preview.append(
                {
                    "标的": sym,
                    "纪律状态": sig.action_zh,
                    "信心": sig.confidence,
                    "趋势分": sig.trend_score,
                    "RS vs QQQ": round(sig.rs_vs_qqq_pct, 1),
                    "Pivot": sig.pivot,
                    "参考止损": sig.stop,
                    "R:R": round(sig.reward_risk, 2) if sig.reward_risk else None,
                    "1%风险股数": sig.suggest_shares,
                    "提示": sig.status_zh + ("；" + sig.fomo_hint if sig.fomo_hint else ""),
                }
            )
        if preview:
            st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)
        else:
            st.info("当前预览列表为空（可能全是已持仓标的）。")

    _render_simple_watchlist_sections(ranked_rows, held_map)
    st.divider()
    st.markdown("### 高级评分队列")
    st.caption("所有卡片与上面的稳健票库共用同一套 DecisionCard；这里只是按队列展开 Q/T/P/C 细节。")

    _render_summary(rows, weights)
    st.caption("买入区间用于判断未持有/补仓时机；已持有是否卖出请看持仓纪律（仓位、成本、回撤、趋势）。")

    unheld_rows = [r for r in ranked_rows if r.symbol not in held_map]
    can_buy = [r for r in unheld_rows if str(getattr(r, "current_action", "")) in {"priority_buyable", "buyable"}]
    wait_pullback = [r for r in unheld_rows if getattr(r, "decision_queue", "") == "好票等回踩"]
    observe = [r for r in unheld_rows if getattr(r, "decision_queue", "") == "小仓观察"][:8]
    hidden = [r for r in unheld_rows if getattr(r, "decision_queue", "") == "暂不看"]

    st.markdown("### ✅ 现在可分批")
    st.caption("这些标的已经进入推荐区间，质量/趋势没有明显问题；适合小额分批，不是一次性梭哈。")
    st.caption(" / ".join([r.symbol for r in can_buy[:8]]) or "暂无")
    for i, r in enumerate(can_buy[:4]):
        _render_card(r, held_map.get(r.symbol, {"is_held": False}), key_prefix=f"now_{i}")

    st.markdown("### 🔔 好票等回踩")
    st.caption("标的不错，但当前位置偏高或组合已偏重；不追，只设回踩提醒。")
    st.caption(" / ".join([r.symbol for r in wait_pullback[:8]]) or "暂无")
    for i, r in enumerate(wait_pullback[:4]):
        _render_card(r, held_map.get(r.symbol, {"is_held": False}), key_prefix=f"wait_{i}")

    st.markdown("### 👀 小仓观察")
    st.caption("质量尚可但趋势/催化/位置还没给出强动作，先观察。")
    st.caption(" / ".join([r.symbol for r in observe[:8]]) or "暂无")
    if hidden:
        with st.expander(f"暂不看 / 折叠（{len(hidden)}）", expanded=False):
            st.caption("质量、趋势、位置或板块权重暂时不合适；默认不占首页空间。")
            st.write(" / ".join([r.symbol for r in hidden[:40]]))

    left, right = st.columns([4, 1.25])
    with right:
        _render_gap_panel(weights)
        st.markdown("#### 智能提醒中心")
        st.caption(
            "读取库内 `price_zone` + `book_system`：几何区间（回踩/跌破/ETF 额外加仓/typed 观察）、纪律信号"
            "（ENTER_BUY_ZONE / ENTER_EXTRA_DCA_ZONE / NEAR_PIVOT / BREAKOUT / EXTENDED / STOP / REDUCE 等）。打开选股/持仓页时同步，冷却窗内去重。"
        )
        pz_rows = fetch_recent_price_zone_alerts(8)
        if pz_rows:
            for a in pz_rows:
                sym = a.get("symbol") or ""
                cat = a.get("category") or ""
                st.markdown(
                    f"**{a.get('title', '')}**  \n{a.get('what_happened', '')}  \n"
                    f"`{sym}` · {a.get('occurred_at', '')} · `{cat}`"
                )
        else:
            st.caption("暂无记录：未命中条件或仍在冷却窗内。")
        hot = [r for r in rows if r.label == "重估趋势"][:4]
        if hot:
            st.caption("重估趋势候选 · 提醒文案预设（不等同于已写入 alerts）：")
            for r in hot:
                st.caption(f"{r.symbol}: {r.alert_suggestions[0] if r.alert_suggestions else '设回踩提醒'}")

    with left:
        all_buckets = sorted({r.bucket for r in rows})
        default = [b for b in all_buckets if b != "科技 / AI / 半导体"][:6] or all_buckets
        chosen = st.multiselect("板块筛选", all_buckets, default=default)
        advice_filter = st.radio(
            "建议筛选",
            ["全部", "现在可分批", "等回踩", "重大异动", "低配板块", "ETF", "大佬持仓", "已收藏", "可分批", "偏高不追", "只适合定投", "小仓观察"],
            index=0,
            horizontal=True,
        )

        filtered = [r for r in rows if r.bucket in set(chosen)]
        if advice_filter == "现在可分批":
            filtered = [r for r in filtered if str(getattr(r, "current_action", "")) in {"priority_buyable", "buyable"}]
        elif advice_filter == "等回踩":
            filtered = [r for r in filtered if str(getattr(r, "current_action", "")) in {"wait_pullback", "dca_only"}]
        elif advice_filter == "重大异动":
            filtered = [r for r in filtered if "重大异动" in str(r.advice) or abs(float(getattr(r, "chg_pct", 0.0))) >= 8]
        elif advice_filter == "低配板块":
            filtered = [r for r in filtered if _bucket_gap_bonus(str(r.bucket), weights) > 0]
        elif advice_filter == "ETF":
            filtered = [r for r in filtered if str(r.bucket) == "ETF / 大盘底仓" or str(r.symbol).upper() in {"VOO", "VTI", "SPY", "IVV", "QQQ", "QQQM", "SCHD"}]
        elif advice_filter == "大佬持仓":
            filtered = [r for r in filtered if int(getattr(r, "guru_score", 0)) > 0]
        elif advice_filter == "已收藏":
            filtered = [r for r in filtered if _starred(str(r.symbol))]
        elif advice_filter == "可分批":
            filtered = [r for r in filtered if str(getattr(r, "current_action", "")) in {"priority_buyable", "buyable"}]
        elif advice_filter == "只适合定投":
            filtered = [r for r in filtered if str(getattr(r, "current_action", "")) == "dca_only"]
        elif advice_filter == "小仓观察":
            filtered = [r for r in filtered if getattr(r, "decision_queue", "") == "小仓观察"]
        elif advice_filter != "全部":
            filtered = [r for r in filtered if r.advice == advice_filter]
        filtered = sorted(filtered, key=lambda r: _compute_display_rank(r, weights), reverse=True)
        if not filtered:
            st.info("当前筛选下暂无候选。")
            return

        grouped: dict[str, list[OpportunityRow]] = defaultdict(list)
        for row in filtered:
            grouped[row.bucket].append(row)
        descs = _bucket_descs()
        for bucket in chosen:
            sub = grouped.get(bucket, [])
            if sub:
                _render_bucket_section(bucket, sub, descs.get(bucket, ""))

    st.markdown("### 已持有 · 持仓纪律")
    held_rows = [r for r in ranked_rows if r.symbol in held_map]
    if not held_rows:
        st.caption("当前候选中暂无已持有标的。")
    else:
        for i, r in enumerate(held_rows[:8]):
            _render_card(r, held_map.get(r.symbol, {"is_held": False}), key_prefix=f"held_{i}")

    st.divider()
    st.subheader("🧠 大佬作业池（灵感源）")
    cfg = _load_guru_watchlist_safe()
    st.caption(
        str(
            cfg.get("disclaimer")
            or "大佬持仓只用于候选灵感，不是直接买入信号。最终仍看区间、仓位、新闻与风险。"
        )
    )
    themes: dict[str, list] = defaultdict(list)
    for r in ranked_rows:
        theme = str(getattr(r, "guru_theme", "") or "").strip()
        if theme:
            themes[theme].append(r)
    if not themes:
        st.caption("当前候选没有命中作业池映射。")
    else:
        for theme in sorted(themes.keys()):
            st.markdown(f"### {theme}")
            picks = themes[theme][:6]
            st.caption(
                " / ".join(
                    [
                        f"{x.symbol}({_guru_evidence_label_safe(guru_meta.get(str(x.symbol).upper()), 1) if guru_meta.get(str(x.symbol).upper()) else '作业池'})"
                        for x in picks
                    ]
                )
            )
            cols = st.columns(2)
            for i, row in enumerate(picks):
                with cols[i % 2]:
                    _render_card(
                        row,
                        held_map.get(row.symbol, {"is_held": False}),
                        key_prefix=f"guru_{theme}_{i}",
                    )

    guru_zone_hits = [
        r
        for r in ranked_rows
        if int(getattr(r, "guru_score", 0)) > 0
        and str(getattr(r, "current_action", "")) in {"priority_buyable", "buyable"}
    ]
    if guru_zone_hits:
        st.caption("进入推荐区间的大佬候选：" + " / ".join([r.symbol for r in guru_zone_hits[:8]]))
        if st.button("同步大佬区间提醒到 alerts", key="guru_zone_alerts_sync"):
            n = _push_guru_zone_alerts(guru_zone_hits, weights)
            if n > 0:
                st.success(f"已写入 {n} 条提醒到 alerts。")
            else:
                st.info("今天这些提醒已存在，无新增。")

    with st.expander("旧版稳健推荐表（仅调试参考）", expanded=False):
        st.caption("为避免和 DecisionCard 冲突，日常决策不再读取这张旧表；它只用于检查旧推荐任务是否还在产出。")
        recs = generate_recommendation_list()
        if not recs:
            st.caption("暂无稳健推荐数据。")
            return
        rdf = pd.DataFrame([r.__dict__ for r in recs])
        st.dataframe(
            rdf[["symbol", "name", "sector", "stability_score", "growth_score", "valuation_zone"]].rename(
                columns={
                    "symbol": "代码",
                    "name": "名称",
                    "sector": "行业",
                    "stability_score": "稳健分",
                    "growth_score": "成长分",
                    "valuation_zone": "估值标签",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
