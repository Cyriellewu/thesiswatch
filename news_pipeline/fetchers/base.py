"""News fetcher base classes."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import md5

log = logging.getLogger(__name__)


@dataclass
class RawNews:
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
    primary_ticker: str | None = None
    affected_tickers: list[str] = field(default_factory=list)
    image_url: str | None = None
    raw_payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.published_at = ensure_utc(self.published_at)

    @property
    def id(self) -> str:
        return md5(self.url.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "summary": self.summary,
            "primary_ticker": self.primary_ticker,
            "affected_tickers": self.affected_tickers,
            "image_url": self.image_url,
        }


class BaseFetcher(ABC):
    name: str = "base"
    rate_limit_seconds: float = 1.0

    @abstractmethod
    def fetch(self, **kwargs) -> list[RawNews]:
        ...

    def safe_fetch(self, **kwargs) -> list[RawNews]:
        try:
            results = self.fetch(**kwargs)
            log.info("[%s] fetched %s news", self.name, len(results))
            return results
        except Exception as exc:
            log.exception("[%s] fetch failed: %s", self.name, exc)
            return []


def ensure_utc(value: datetime | str | int | float | None) -> datetime:
    """Normalize all source datetimes to timezone-aware UTC.

    Some feeds return offset-naive UTC-like timestamps, while others return
    offset-aware ISO values. Dedupe/clustering subtracts datetimes, so mixing
    those two forms crashes in Python.
    """

    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return datetime.now(timezone.utc)
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
