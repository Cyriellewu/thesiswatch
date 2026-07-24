"""✏️ 编辑我的持仓 — in-app editor for the user's private watchlist.

Reads/writes the gitignored `config/watchlist.yaml` (seeded from the example on
first run). Lets any user fill in their own holdings + cash without touching
YAML by hand, so the repo ships with only sample data.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
import yaml

from data_layer.watchlist_resolver import watchlist_path


def _load() -> tuple[float, pd.DataFrame]:
    p = watchlist_path()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cash = 0.0
    for k in ("account_cash_usd", "cash_usd", "cash"):
        if raw.get(k) is not None:
            cash = float(raw[k])
            break
    rows = [
        {
            "ticker": str(s.get("ticker", "")).upper(),
            "qty": float(s.get("qty", 0) or 0),
            "avg_cost_usd_per_share": float(s.get("avg_cost_usd_per_share", 0) or 0),
        }
        for s in (raw.get("symbols") or [])
    ]
    if not rows:
        rows = [{"ticker": "", "qty": 0.0, "avg_cost_usd_per_share": 0.0}]
    return cash, pd.DataFrame(rows)


def _save(cash: float, df: pd.DataFrame) -> int:
    symbols = []
    for _, r in df.iterrows():
        tk = str(r.get("ticker") or "").upper().strip()
        if not tk:
            continue
        symbols.append({
            "ticker": tk,
            "qty": float(r.get("qty") or 0),
            "avg_cost_usd_per_share": float(r.get("avg_cost_usd_per_share") or 0),
        })
    payload = {"account_cash_usd": float(cash or 0), "symbols": symbols}
    watchlist_path().write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return len(symbols)


def render() -> None:
    st.markdown("### ✏️ 编辑我的持仓")
    st.caption(
        "在这里填你自己的持仓。保存到本地的 `config/watchlist.yaml`（已被 gitignore，不会进仓库、不会公开）。"
        "首次运行是示例组合，改成你真实的即可。"
    )

    cash0, df0 = _load()
    cash = st.number_input("账户现金 (USD)", min_value=0.0, value=float(cash0), step=100.0, format="%.2f")
    st.markdown("**持仓明细**（可增删行）")
    edited = st.data_editor(
        df0,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "ticker": st.column_config.TextColumn("代码", help="美股 ticker，如 AAPL"),
            "qty": st.column_config.NumberColumn("股数", min_value=0.0, step=0.01, format="%.4f"),
            "avg_cost_usd_per_share": st.column_config.NumberColumn("平均成本$/股", min_value=0.0, step=0.01, format="%.2f"),
        },
        key="portfolio_editor",
    )

    c1, c2 = st.columns([1, 3])
    if c1.button("💾 保存持仓", use_container_width=True):
        n = _save(cash, edited)
        # bust cached advice so the app recomputes with the new portfolio
        for mod in ("tab_agent_home", "tab_committee"):
            try:
                m = __import__(f"ui.{mod}", fromlist=["_advice_cached"])
                m._advice_cached.clear()
            except Exception:
                pass
        st.success(f"已保存 {n} 只持仓。回到首页会用新组合重算。")
    c2.caption("提示：这份文件只在你本机，公开仓库里只有示例组合 `watchlist.example.yaml`。")
