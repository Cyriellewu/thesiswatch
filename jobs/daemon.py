#!/usr/bin/env python3
"""
常驻后台：**NYSE 交易日的美国东部时间** 自动跑一次完整闭环：

    market_pulse → 机会池打分 → alerts → （可选）ntfy → run_agent_cycle → 权益快照

与 Streamlit 无关；只打开网页不会触发本调度。

用法::

    cd ~/alphawatch && source .venv/bin/activate
    python -m jobs.daemon

时间来自 ``config/settings.yaml`` 中的 ``daemon.open_et`` / ``daemon.close_et``（默认 10:00 / 15:30 ET）。

------------------------------------------------------------
SQLite 自检（列名与库表一致；请在 ``sqlite3`` 或 GUI 里对 ``alphawatch.db`` 执行）::

    SELECT id, label, mode, starting_cash_usd, cash_usd, allow_trades
    FROM agent_accounts ORDER BY id;

    SELECT datetime(created_at) AS t, account_id, action, symbol, dollar_amount,
           plain_reason, plain_risk
    FROM agent_decisions
    ORDER BY id DESC LIMIT 20;

    SELECT datetime(created_at) AS t, account_id, action, symbol, qty, px,
           notional_usd, COALESCE(plain_reason, reason) AS why_plain
    FROM agent_trades
    ORDER BY id DESC LIMIT 20;

    SELECT datetime(created_at) AS t, account_id, equity_usd, cash_usd, positions_mv_usd
    FROM agent_equity_snapshots
    ORDER BY id DESC LIMIT 20;
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NY = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

from dotenv import load_dotenv  # noqa: E402

# Prefer project-local .env values for daemon consistency.
load_dotenv(ROOT / ".env", override=True)


def _parse_hh_mm(raw: str) -> tuple[int, int]:
    s = raw.strip()
    parts = s.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"bad time string: {raw!r}")
    return h, m


def is_equity_trading_day(d: date) -> bool:
    """NYSE 常规交易日（含交易所日程中的休市日剔除）。"""
    # 依赖 exchange_calendars；不可用时退化到工作日 Mon–Fri。
    try:
        import exchange_calendars as xcals  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415

        cal = xcals.get_calendar("XNYS")
        ts = pd.Timestamp(d.isoformat(), tz="America/New_York")
        return bool(cal.is_session(ts))
    except Exception:
        logging.debug("NYSE calendar fallback to Mon–Fri", exc_info=True)
        return d.weekday() < 5


def next_pulse_et(slots: list[tuple[int, int]], *, now: datetime | None = None) -> datetime:
    """给定当天多个 (时, 分) 触发点，返回严格晚于 ``now`` 的下一个交易日触发时刻。"""
    now = now or datetime.now(NY)
    d = now.date()
    for _ in range(28):
        if is_equity_trading_day(d):
            for hh, mm in slots:
                slot = datetime.combine(d, time(hh, mm), tzinfo=NY)
                if slot > now:
                    return slot
        d += timedelta(days=1)
    raise RuntimeError("could not find next scheduled pulse within 28 days")


def run_market_pulse_job(tag: str, *, ntfy_limit: int = 8) -> None:
    """单次：交易日才跑；包含 agent + 快照。"""
    now_et = datetime.now(NY)
    if not is_equity_trading_day(now_et.date()):
        logging.info("skip pulse %s: not an NYSE session (%s)", tag, now_et.date().isoformat())
        return

    from db.client import bootstrap_database, get_conn  # noqa: PLC0415
    from jobs.daily_brief import build_daily_direction_brief  # noqa: PLC0415
    from jobs.market_pulse import run_market_pulse  # noqa: PLC0415
    from jobs.news_cycle import run_real_news_cycle  # noqa: PLC0415
    from monitors.market_brake import maybe_push_market_brake  # noqa: PLC0415
    from monitors.auto_watch_config import push_levels_from_config  # noqa: PLC0415
    from agents.runner import run_all_agents  # noqa: PLC0415
    from push.alert_pipeline import flush_pending_ntfy  # noqa: PLC0415
    from push.notify import send_daily_direction_brief  # noqa: PLC0415

    bootstrap_database()
    cn = get_conn()
    try:
        # Phase 2 policy:
        # - daily concise brief once
        # - extra alerts only for red-grade events (urgent)
        stats = run_market_pulse(conn=cn, push_ntfy=False, ntfy_limit=ntfy_limit, run_agents=False)
        cn.commit()

        # Real news fetch. LLM tagging/storylines are opt-in via
        # ALPHA_NEWS_AUTO_LLM=1 or the dashboard deep-analysis buttons.
        # The default news UI is rule-first Daily Market Radar.
        stats["news_cycle"] = run_real_news_cycle()
        stats["market_brake_pushed"] = maybe_push_market_brake(cn)

        from tasks.weekly_dca import run_weekly_dca_check

        stats["weekly_dca"] = run_weekly_dca_check(now_et)

        stats["agent_cycle"] = run_all_agents(conn=cn)
        push_levels = push_levels_from_config()
        if tag != "open" and push_levels:
            push_levels = tuple(x for x in push_levels if x == "urgent") or push_levels
        pushed_n = 0 if not push_levels else flush_pending_ntfy(cn, limit=ntfy_limit, levels=push_levels)
        stats["ntfy_levels"] = ",".join(push_levels)
        stats["ntfy_pushed_by_level"] = pushed_n

        if tag == "open":
            dkey = "daily_direction_brief_date_et"
            today_s = now_et.date().isoformat()
            prev = cn.execute("SELECT v FROM meta_kv WHERE k = ?", (dkey,)).fetchone()
            if not prev or str(prev["v"]) != today_s:
                body = build_daily_direction_brief()
                ok = send_daily_direction_brief(body)
                if ok:
                    cn.execute(
                        """INSERT INTO meta_kv (k, v, updated_at) VALUES (?,?,datetime('now'))
                           ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = datetime('now')""",
                        (dkey, today_s),
                    )
                    stats["daily_direction_sent"] = 1
                else:
                    stats["daily_direction_sent"] = 0
        cn.commit()
        logging.info("pulse %s done: %s", tag, stats)
    finally:
        cn.close()


def _load_daemon_settings() -> tuple[str, list[tuple[int, int]], int, int]:
    import yaml  # noqa: PLC0415

    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    tz = (cfg.get("app") or {}).get("timezone_display") or "America/New_York"
    block = (cfg.get("daemon") or {}) if isinstance(cfg.get("daemon"), dict) else {}
    open_s = str(block.get("open_et") or "10:00").strip()
    close_s = str(block.get("close_et") or "15:30").strip()
    ntfy_limit = int(block.get("ntfy_limit") or 8)
    slots = [_parse_hh_mm(open_s), _parse_hh_mm(close_s)]
    dca_block = (cfg.get("weekly_dca") or {}) if isinstance(cfg.get("weekly_dca"), dict) else {}
    dca_interval = int(dca_block.get("check_interval_minutes") or 30)
    return tz, slots, ntfy_limit, dca_interval


def _print_banner(slots: list[tuple[int, int]], tz_name: str) -> None:
    next_t = next_pulse_et(slots)
    print("AlphaWatch daemon running", flush=True)
    print(f"Timezone: {tz_name}", flush=True)
    print(f"Next market pulse: {next_t.strftime('%Y-%m-%d %H:%M')} ET", flush=True)
    print(f"Next agent cycle: {next_t.strftime('%Y-%m-%d %H:%M')} ET", flush=True)
    print("(market pulse 与 agent 同一趟执行；Ctrl+C 停止)", flush=True)


def run_weekly_dca_job() -> None:
    from tasks.weekly_dca import run_weekly_dca_check

    stats = run_weekly_dca_check()
    logging.info("weekly DCA job: %s", stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaWatch 常驻：交易日 ET 自动 market_pulse + agents")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一次（仍会跳过非交易日），用于手工验证",
    )
    args = parser.parse_args()

    tz_name, slots, ntfy_limit, dca_interval = _load_daemon_settings()

    if args.once:
        run_market_pulse_job("once", ntfy_limit=ntfy_limit)
        return

    from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: PLC0415
    from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415

    _print_banner(slots, tz_name)

    sched = BlockingScheduler(timezone=tz_name)
    for label, (hh, mm) in (("open", slots[0]), ("close", slots[1])):
        sched.add_job(
            run_market_pulse_job,
            CronTrigger(day_of_week="mon-fri", hour=hh, minute=mm, timezone=tz_name),
            args=[label],
            kwargs={"ntfy_limit": ntfy_limit},
            id=f"alphawatch_daemon_{label}",
            replace_existing=True,
        )
        logging.info("Scheduled %s at %02d:%02d %s (Mon–Fri; NYSE holiday skip in job)", label, hh, mm, tz_name)

    dca_minutes = ",".join(str(m) for m in range(0, 60, max(1, dca_interval)))
    sched.add_job(
        run_weekly_dca_job,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute=dca_minutes, timezone=tz_name),
        id="alphawatch_weekly_dca",
        replace_existing=True,
    )
    logging.info("Scheduled weekly DCA checks Mon–Fri 10:00–15:59 ET every %s min", dca_interval)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Daemon stopped")


if __name__ == "__main__":
    main()
