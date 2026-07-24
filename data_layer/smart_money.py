from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InvestorPositionDelta:
    investor: str
    ticker: str
    delta_shares_notes: str


def thirteenth_f_demo() -> list[InvestorPositionDelta]:
    """Wire SEC EDGAR pull + parser in production."""

    return [
        InvestorPositionDelta("Fund A demo", "PLTR", "+new"),
        InvestorPositionDelta("Fund B demo", "OKLO", "+add"),
    ]
