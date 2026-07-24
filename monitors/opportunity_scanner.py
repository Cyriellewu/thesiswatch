from __future__ import annotations

from data_layer.smart_money import thirteenth_f_demo


def run_opportunity_scanner() -> dict:
    return {
        "smart_money": [vars(x) for x in thirteenth_f_demo()],
        "rule_radar": [],
    }
