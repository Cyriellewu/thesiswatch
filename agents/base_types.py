from __future__ import annotations

from abc import ABC, abstractmethod


class AgentStyle(ABC):
    slug: str = "base"

    @abstractmethod
    def rationale(self, context: dict) -> str:
        raise NotImplementedError
