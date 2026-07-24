from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def configured() -> bool:
    ak = os.environ.get("ALPACA_API_KEY", "").strip()
    sk = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    return bool(ak and sk)


def get_paper_account_snapshot() -> dict[str, Any] | None:
    """Read-only Alpaca Paper account snapshot for dashboards."""

    if not configured():
        return None

    try:
        from alpaca.trading.client import TradingClient  # noqa: PLC0415
    except ImportError:
        logger.warning("alpaca-py not installed; pip install alpaca-py")
        return None

    ak = os.environ.get("ALPACA_API_KEY", "").strip()
    sk = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    base = os.environ.get("ALPACA_BASE_URL", "").strip().lower()

    paper = True
    if base and "paper-api" not in base and "/paper" not in base and "sandbox" not in base:
        if "alpaca.markets" in base and "paper" not in base.split("//", 2)[-1]:
            logger.warning(
                "ALPACA_BASE_URL looks like LIVE trading host; overriding to paper-only client."
            )

    try:
        client = TradingClient(ak, sk, paper=paper)
        acc = client.get_account()
        return {
            "status": getattr(acc, "status", ""),
            "equity": float(acc.equity),
            "cash": float(acc.cash),
            "portfolio_value": float(acc.portfolio_value),
            "buying_power": float(acc.buying_power),
            "currency": getattr(acc, "currency", "USD"),
        }
    except Exception:
        logger.exception("Alpaca get_account failed")
        return None
