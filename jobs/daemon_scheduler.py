#!/usr/bin/env python3
"""
常驻调度：每天在 ``config/settings.yaml`` 配置的时间跑一次 **market pulse**
（SQLite 入库 → `alerts` → ntfy，最多若干条告警）。

⚠️ 只有**常驻进程**会在到点触发；只开 Streamlit 页面**不会**自动推送。

推荐使用（NYSE 交易日 10:00 / 15:30 ET（可配）market_pulse **含虚拟 agent**）::

    cd ~/alphawatch && source .venv/bin/activate
    python -m jobs.daemon

早间简单 digest（沿用下方配置时间，不接 agent）仍可用::

    python jobs/daemon_scheduler.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)


def _parse_hh_mm(raw: str) -> tuple[int, int]:
    s = raw.strip()
    parts = s.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time string: {raw!r}")
    return h, m


def morning_job() -> None:
    """早盘统一脉冲：报价/新闻入库 → alerts →（若配置）ntfy."""

    from db.client import bootstrap_database, get_conn
    from jobs.market_pulse import run_market_pulse

    bootstrap_database()
    cn = get_conn()
    try:
        stats = run_market_pulse(
            conn=cn, push_ntfy=True, ntfy_limit=6, run_agents=True,
        )
        cn.commit()
        logging.info("morning_job market_pulse %s", stats)
        try:
            from tasks.willow_agent import push_daily_digest  # noqa: PLC0415

            pushed = push_daily_digest()
            logging.info("morning_job willow digest pushed=%s", pushed)
        except Exception:
            logging.exception("willow digest push failed")
    finally:
        cn.close()


def main() -> None:
    import yaml
    from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: PLC0415
    from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415

    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    tz = (cfg.get("app") or {}).get("timezone_display") or "America/New_York"
    morning = ((cfg.get("scheduler") or {}).get("morning_brief_local") or "07:00").strip()
    hour, minute = _parse_hh_mm(morning)

    sched = BlockingScheduler(timezone=tz)
    sched.add_job(
        morning_job,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="alphawatch_morning_ntfy",
        replace_existing=True,
    )
    logging.info(
        "Cron: market pulse + ntfy every day at %02d:%02d (%s)",
        hour,
        minute,
        tz,
    )
    print("Daemon running. Ctrl+C to stop. Morning market pulse at", morning, tz, flush=True)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped")


if __name__ == "__main__":
    main()
