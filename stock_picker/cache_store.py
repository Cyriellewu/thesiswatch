from __future__ import annotations

from data_layer.cache import JsonTTLCache

_WEEK = 7 * 24 * 3600


def load_weekly_blurb(symbol: str) -> str | None:
    c = JsonTTLCache("stock_picker_weekly_blurb", _WEEK)
    return c.get(c.key(str(symbol).upper().strip()))


def save_weekly_blurb(symbol: str, text: str) -> None:
    c = JsonTTLCache("stock_picker_weekly_blurb", _WEEK)
    c.set(c.key(str(symbol).upper().strip()), str(text).strip())

