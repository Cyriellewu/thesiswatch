"""Radar / alert price labels by listing currency (no heavy deps)."""

from __future__ import annotations


def quote_currency_for_symbol(sym: str) -> str:
    u = sym.upper()
    if u.endswith((".KS", ".KQ")):
        return "KRW"
    if u.endswith(".HK"):
        return "HKD"
    if u.endswith((".T", ".JP")):
        return "JPY"
    if u.endswith((".SS", ".SZ")):
        return "CNY"
    if u.endswith(".TW"):
        return "TWD"
    return "USD"


def local_quote_currency(sym: str) -> str | None:
    ccy = quote_currency_for_symbol(sym)
    return ccy if ccy != "USD" else None


def format_radar_price_line(
    *,
    symbol: str,
    price: float,
    day_change_pct: float,
    day_change_abs: float,
) -> tuple[str, str, str]:
    """Return (price_label, abs_change_label, one_line_plain)."""
    ccy = quote_currency_for_symbol(symbol)
    if ccy == "KRW":
        p = f"₩{price:,.0f}"
        a = f"₩{day_change_abs:+,.0f}"
    elif ccy == "JPY":
        p = f"¥{price:,.0f}"
        a = f"¥{day_change_abs:+,.0f}"
    elif ccy == "HKD":
        p = f"HK${price:,.2f}"
        a = f"HK${day_change_abs:+,.2f}"
    elif ccy in {"CNY", "TWD"}:
        unit = "¥" if ccy == "CNY" else "NT$"
        p = f"{unit}{price:,.2f}"
        a = f"{unit}{day_change_abs:+,.2f}"
    else:
        p = f"${price:,.2f}"
        a = f"${day_change_abs:+,.2f}"
    line = f"{symbol} 今日 {day_change_pct:+.1f}%（{a}），当前 {p}。"
    return p, a, line
