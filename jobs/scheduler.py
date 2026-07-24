from __future__ import annotations

"""Cron-friendly entry points — expand with APScheduler once stable."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from monitors import earnings_radar, holdings_monitor, macro_sentinel, opportunity_scanner  # noqa: E402
from push.notify import send_morning_digest  # noqa: E402


def daily_refresh(symbols: list[str]) -> dict:
    payload = holdings_monitor.run_holdings_pulse(symbols)
    payload["macro"] = macro_sentinel.run_macro_sentinel()
    payload["earnings"] = earnings_radar.run_earnings_radar(symbols)
    payload["opportunities"] = opportunity_scanner.run_opportunity_scanner()
    return payload


def daily_refresh_ntfy_digest(symbols: list[str]) -> tuple[dict, bool]:
    payload = daily_refresh(symbols)
    return payload, send_morning_digest(payload)


def _symbols_default() -> list[str]:
    import yaml

    data = yaml.safe_load((ROOT / "config" / "watchlist.yaml").read_text(encoding="utf-8"))
    return [str(row["ticker"]).upper() for row in data.get("symbols") or [] if row.get("ticker")]


if __name__ == "__main__":
    symbols = _symbols_default()
    _payload, ok = daily_refresh_ntfy_digest(symbols)
    print("AlphaWatch: ntfy digest", "ok" if ok else "failed")
