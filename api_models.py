from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

DataState = Literal["ok", "stale", "partial", "unavailable"]
Mode = Literal["demo", "live"]

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    state: DataState
    mode: Mode
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
