from __future__ import annotations

from agents.base_types import AgentStyle


class ConservativeAgent(AgentStyle):
    slug = "conservative"

    def rationale(self, context: dict) -> str:
        return (
            "Conservative: prioritize drawdown caps, widen stops, emphasize cash buffer when macro flags fire."
            f" Context keys present: {', '.join(sorted(context.keys()))}."
        )
