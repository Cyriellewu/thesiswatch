"""Run the real-news fetch cycle, with optional LLM analysis.

Pipeline:
1. fetch raw news into SQLite `news`
2. optionally tag pending news with LLM (`category`, `severity`, `one_line_zh`)
3. optionally generate daily storylines

The daemon runs the full fetch -> tag -> storyline cycle by default so the
dashboard has plain-language summaries. Set ALPHA_NEWS_AUTO_LLM=0 to make the
news tab rule-only and reserve LLM analysis for explicit deep-analysis actions.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from db.client import default_db_path
from data_layer.universe import load_opportunity_tickers, load_watchlist_tickers
from news_pipeline.news_tagger import get_pending_news, run_news_tagging
from news_pipeline.orchestrator import run_news_pipeline
from news_pipeline.storyline_generator import run_storyline_pipeline

log = logging.getLogger(__name__)


def run_real_news_cycle(
    db_path: str | Path | None = None,
    *,
    days_back: int = 1,
    tag_batch_size: int | None = None,
    tag_max_batches: int | None = None,
    run_llm: bool | None = None,
) -> dict[str, Any]:
    """Run fetch, optionally tag/storyline, and return structured stats."""

    db = Path(db_path) if db_path is not None else default_db_path()
    holdings = sorted({s.upper() for s in load_watchlist_tickers()})
    tickers = sorted({*holdings, *[s.upper() for s in load_opportunity_tickers()]})
    batch_size = tag_batch_size or int(os.environ.get("ALPHA_NEWS_TAG_BATCH_SIZE", "8"))
    max_batches = tag_max_batches or int(os.environ.get("ALPHA_NEWS_TAG_MAX_BATCHES", "15"))
    target_pending = int(os.environ.get("ALPHA_NEWS_TAG_TARGET_PENDING", "10"))
    max_rounds = int(os.environ.get("ALPHA_NEWS_TAG_MAX_ROUNDS", "10"))
    if run_llm is None:
        run_llm = os.environ.get("ALPHA_NEWS_AUTO_LLM", "1") == "1"

    log.info("news cycle step 1: fetching real news for %s tickers", len(tickers))
    fetch_stats = run_news_pipeline(db, tickers, days_back=days_back)
    log.info("news cycle fetch stats: %s", fetch_stats)

    tag_stats: dict[str, Any] = {"skipped": True, "reason": "ALPHA_NEWS_AUTO_LLM=0"}
    storyline_stats: dict[str, Any] = {"skipped": True, "reason": "ALPHA_NEWS_AUTO_LLM=0"}
    if run_llm:
        log.info("news cycle step 2: tagging news with LLM")
        rounds: list[dict[str, Any]] = []
        for idx in range(max_rounds):
            pending = len(get_pending_news(db, limit=target_pending + 1))
            if pending <= target_pending:
                break
            result = run_news_tagging(
                db,
                holdings,
                batch_size=batch_size,
                max_batches=max_batches,
            )
            rounds.append(result)
            if int(result.get("tagged") or 0) <= 0:
                break
            log.info("news tagging round %s/%s: %s", idx + 1, max_rounds, result)
        remaining = len(get_pending_news(db, limit=target_pending + 1))
        tag_stats = {
            "rounds": len(rounds),
            "tagged": sum(int(r.get("tagged") or 0) for r in rounds),
            "errors": sum(int(r.get("errors") or 0) for r in rounds),
            "remaining_check": remaining,
            "target_pending": target_pending,
            "round_details": rounds[-3:],
        }
        log.info("news cycle tag stats: %s", tag_stats)

        log.info("news cycle step 3: generating storylines")
        storyline_stats = run_storyline_pipeline(db, holdings)
        if int(storyline_stats.get("count") or 0) == 0 and not storyline_stats.get("skipped"):
            log.error("news cycle storyline generation returned zero; retrying once")
            time.sleep(5)
            retry_stats = run_storyline_pipeline(db, holdings)
            storyline_stats = {"first_attempt": storyline_stats, "retry": retry_stats, **retry_stats}
        log.info("news cycle storyline stats: %s", storyline_stats)

        from tasks.news_instinct_push import push_news_instinct_alerts

        instinct_stats = push_news_instinct_alerts(db, holdings)
        tag_stats["instinct_push"] = instinct_stats
        log.info("news instinct push stats: %s", instinct_stats)

    return {
        "fetch": fetch_stats,
        "tag": tag_stats,
        "storyline": storyline_stats,
        "holdings": holdings,
        "tickers": len(tickers),
    }
