"""VCP-like contraction probe from OHLCV (启发式，非形态识别 ML)。"""

from __future__ import annotations

from typing import Any

import pandas as pd


def vcp_probe(h: pd.DataFrame | None) -> dict[str, Any]:
    """用近端三段窗口的高低点差 + 成交量均量，粗判回调是否在收敛、量能是否收缩。"""
    empty: dict[str, Any] = {
        "pullbacks_pct": [],
        "vol_ratio_tail_vs_head": None,
        "vol_shrinking": False,
        "pullbacks_shrinking": False,
        "label": "数据不足",
        "lines": [],
    }
    if h is None or h.empty or len(h) < 90:
        return empty
    if not {"High", "Low"}.issubset(h.columns):
        return empty

    tail = h.tail(120).reset_index(drop=True)
    high = tail["High"].astype(float)
    low = tail["Low"].astype(float)
    m = len(tail)
    w = max(20, m // 3)
    dds: list[float] = []
    vmeans: list[float] = []
    for k in range(3):
        lo = k * w
        hi = m if k == 2 else (k + 1) * w
        seg_h = high.iloc[lo:hi]
        seg_l = low.iloc[lo:hi]
        hh = float(seg_h.max())
        ll = float(seg_l.min())
        dd = (hh - ll) / hh * 100.0 if hh > 0 else 0.0
        dds.append(dd)
        if "Volume" in tail.columns:
            vseg = tail["Volume"].astype(float).iloc[lo:hi]
            vmeans.append(float(vseg.mean()) if len(vseg) else 0.0)
        else:
            vmeans.append(0.0)

    pullbacks_shrinking = False
    if len(dds) == 3 and dds[0] > 0 and dds[1] > 0 and dds[2] > 0:
        pullbacks_shrinking = dds[0] > dds[1] * 0.92 and dds[1] > dds[2] * 0.92

    vol_shrinking = False
    vol_ratio = None
    if len(vmeans) == 3 and vmeans[0] > 1e-6:
        vol_ratio = vmeans[2] / vmeans[0]
        vol_shrinking = vmeans[2] < vmeans[0] * 0.85

    lines = [
        f"近 120 日三段回调幅度（段内高低极差）：{dds[0]:.1f}% → {dds[1]:.1f}% → {dds[2]:.1f}%。",
        f"三段均量：尾段/首段 ≈ {vol_ratio:.2f}（<0.85 视为量能收缩）" if vol_ratio is not None else "成交量列缺失，跳过量能收缩判断。",
    ]

    if pullbacks_shrinking and vol_shrinking:
        label = "疑似 VCP-like（启发式）"
    elif pullbacks_shrinking:
        label = "回调在收敛，量能未明确收缩"
    elif vol_shrinking:
        label = "量能偏收缩，价格波动未典型收敛"
    else:
        label = "普通回调 / 非典型收缩"

    return {
        "pullbacks_pct": dds,
        "vol_ratio_tail_vs_head": vol_ratio,
        "vol_shrinking": vol_shrinking,
        "pullbacks_shrinking": pullbacks_shrinking,
        "label": label,
        "lines": lines,
    }
