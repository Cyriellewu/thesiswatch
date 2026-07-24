from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from data_layer.universe import load_watchlist_tickers
from monitors import holdings_monitor
from monitors.macro_sentinel import run_macro_sentinel
from tasks.daily_news_push import generate_daily_news_digest

NY = ZoneInfo("America/New_York")


def build_daily_direction_brief() -> str:
    syms = load_watchlist_tickers()
    pulse = holdings_monitor.run_holdings_pulse(syms)
    qrows = pulse.get("quotes") or []
    macro = run_macro_sentinel().get("strip") or {}

    top = sorted(
        qrows,
        key=lambda x: abs(float((x or {}).get("chg_pct") or 0.0)),
        reverse=True,
    )[:3]

    today = datetime.now(NY).strftime("%m/%d")
    sp = float(macro.get("spy_chg_pct") or 0.0)
    qq = float(macro.get("qqq_chg_pct") or 0.0)
    vix = macro.get("vix")
    teny = macro.get("ten_year_yield_pct")
    if sp >= 0.8 and qq >= 0.8:
        mood = "风险偏好回暖"
    elif sp <= -1.0 or qq <= -1.0:
        mood = "风险偏好降温"
    else:
        mood = "震荡观望"

    lines = [
        f"{today} Daily Market Radar",
        "",
        f"Market: {mood}",
        f"SPY {sp:+.2f}% | QQQ {qq:+.2f}% | VIX {vix} | 10Y {teny}%",
        "",
        "Top moves in your holdings:",
    ]
    for r in top:
        s = str(r.get("symbol") or "")
        c = float(r.get("chg_pct") or 0.0)
        lines.append(f"- {s} {c:+.2f}%")
    lines.append("")
    digest = generate_daily_news_digest(syms)
    sections = [
        ("Important", digest.get("important_alerts") or []),
        ("Holdings", digest.get("holding_direct") or []),
        ("Macro", digest.get("macro_news") or []),
        ("Do not miss", digest.get("opportunities") or []),
    ]
    for title, items in sections:
        lines.append(f"{title}:")
        if not items:
            lines.append("- none")
            lines.append("")
            continue
        for item in items:
            text = item.get("text") or item.get("source_title") or ""
            source = item.get("source_name") or ""
            ts = str(item.get("published_at") or "")[:16].replace("T", " ")
            lines.append(f"- {text} ({source}, {ts})")
        lines.append("")
    lines.append("More details in dashboard tabs. Every news line is source-bound.")
    return "\n".join(lines)
