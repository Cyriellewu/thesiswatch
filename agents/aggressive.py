from __future__ import annotations

from agents.base_types import AgentStyle


class AggressiveAgent(AgentStyle):
    slug = "aggressive"

    def rationale(self, context: dict) -> str:
        return (
            "Aggressive: allow higher turnover on confirmed breakouts while still respecting liquidity."
            f" Context keys present: {', '.join(sorted(context.keys()))}."
        )
