from __future__ import annotations

from data_layer.macro_data import snapshot_macro


def run_macro_sentinel() -> dict:
    snap = snapshot_macro()
    return {"strip": vars(snap), "flags": []}
