from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from db.client import repo_root

DEFAULT_AUTO_WATCH: dict[str, Any] = {
    "enabled": True,
    "scopes": {
        "holdings": True,
        "watchlist": True,
        "opportunity_pool": True,
        "guru_pool": True,
        "revaluation_pool": True,
        "theme_pool": True,
        "etf_pool": True,
    },
    "types": {
        "entry_range": True,
        "major_move": True,
        "theme_breakout": True,
        "holding_drawdown": True,
        "major_news": True,
        "etf_market_drop": True,
    },
    "push_strength": "urgent_major",
    "limits": {
        "max_auto_alerts_per_cycle": 12,
        "max_theme_pushes_per_day": 2,
    },
}


def config_path() -> Path:
    return repo_root() / "config" / "auto_watch.yaml"


def load_auto_watch_config() -> dict[str, Any]:
    p = config_path()
    raw: dict[str, Any] = {}
    if p.exists():
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
    out = dict(DEFAULT_AUTO_WATCH)
    out["scopes"] = {**DEFAULT_AUTO_WATCH["scopes"], **(raw.get("scopes") or {})}
    out["types"] = {**DEFAULT_AUTO_WATCH["types"], **(raw.get("types") or {})}
    out["limits"] = {**DEFAULT_AUTO_WATCH["limits"], **(raw.get("limits") or {})}
    out["enabled"] = bool(raw.get("enabled", DEFAULT_AUTO_WATCH["enabled"]))
    out["push_strength"] = str(raw.get("push_strength") or DEFAULT_AUTO_WATCH["push_strength"])
    return out


def save_auto_watch_config(cfg: dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def auto_watch_enabled() -> bool:
    return bool(load_auto_watch_config().get("enabled"))


def type_enabled(name: str) -> bool:
    cfg = load_auto_watch_config()
    return bool(cfg.get("enabled")) and bool((cfg.get("types") or {}).get(name, True))


def scope_enabled(name: str) -> bool:
    cfg = load_auto_watch_config()
    return bool(cfg.get("enabled")) and bool((cfg.get("scopes") or {}).get(name, True))


def push_levels_from_config() -> tuple[str, ...]:
    cfg = load_auto_watch_config()
    strength = str(cfg.get("push_strength") or "urgent_major")
    if not cfg.get("enabled", True):
        return ()
    if strength == "urgent_only":
        return ("urgent",)
    if strength == "all":
        return ("urgent", "major", "attention", "routine")
    return ("urgent", "major")
