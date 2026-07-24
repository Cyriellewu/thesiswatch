"""Agent decision schemas."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Action(str, Enum):
    BUY = "buy"
    ADD = "add"
    HOLD = "hold"
    TRIM = "trim"
    SELL = "sell"
    WAIT = "wait"


class TradeDecision(BaseModel):
    ticker: str = Field(..., description="Ticker symbol")
    action: Action = Field(..., description="Trade action")
    size_pct: float = Field(0.0, ge=0.0, le=1.0)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    entry_price_max: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    expected_holding_days: int = Field(14, ge=1, le=365)
    reasoning: str = Field(..., min_length=10, max_length=300)
    signals_used: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def _ticker_upper(cls, v: str) -> str:
        return v.strip().upper().lstrip("$")

    @field_validator("reasoning")
    @classmethod
    def _reasoning_not_template(cls, v: str) -> str:
        bad = ("模型返回不是合法", "解释不清", "这轮先不动", "无法分析")
        if any(x in v for x in bad):
            raise ValueError("reasoning contains fallback template")
        return v


class AgentRoundDecision(BaseModel):
    summary: str = Field(..., min_length=10, max_length=200)
    macro_view: Literal["risk_on", "neutral", "risk_off"] = "neutral"
    decisions: list[TradeDecision] = Field(default_factory=list)
    next_check_in_hours: int = Field(24, ge=1, le=168)

    def has_actionable(self) -> bool:
        return any(d.action not in {Action.HOLD, Action.WAIT} for d in self.decisions)
