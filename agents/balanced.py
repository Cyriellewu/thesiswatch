from __future__ import annotations

from agents.base_types import AgentStyle


class BalancedAgent(AgentStyle):
    slug = "balanced"

    def rationale(self, context: dict) -> str:
        return (
            "Balanced: mix trend + mean reversion, cap single-name exposure, mirror benchmark drift."
            f" Context keys present: {', '.join(sorted(context.keys()))}."
        )
