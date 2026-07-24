from __future__ import annotations

from dataclasses import asdict

from data.virtual_accounts import Trade, VirtualAccount, load_virtual_account, save_virtual_account, utc_now
from data_layer.market_data import fetch_quotes
from data_layer.portfolio_analytics import load_positions
from data_layer.universe import load_opportunity_tickers


def _update_position(account: VirtualAccount, symbol: str, shares_delta: float, price: float) -> None:
    sym = symbol.upper()
    pos = account.positions.get(sym, {"shares": 0.0, "avg_cost": 0.0})
    old_shares = float(pos.get("shares") or 0.0)
    old_cost = float(pos.get("avg_cost") or 0.0)
    new_shares = old_shares + shares_delta
    if new_shares <= 1e-9:
        account.positions.pop(sym, None)
        return
    if shares_delta > 0:
        total_cost = old_shares * old_cost + shares_delta * price
        avg = total_cost / new_shares
    else:
        avg = old_cost
    account.positions[sym] = {"shares": round(new_shares, 6), "avg_cost": round(avg, 6)}


def _mark_equity(account: VirtualAccount, px_map: dict[str, float]) -> float:
    mv = 0.0
    for sym, p in account.positions.items():
        mv += float(p.get("shares") or 0.0) * float(px_map.get(sym, 0.0))
    eq = float(account.cash) + mv
    account.equity_curve.append({"ts": utc_now(), "equity": round(eq, 4)})
    account.equity_curve = account.equity_curve[-90:]
    return eq


def init_mirror_account_from_real_positions() -> VirtualAccount:
    account = VirtualAccount(name="mirror_portfolio", initial_cash=0.0, cash=0.0)
    for p in load_positions():
        account.positions[p.ticker.upper()] = {"shares": float(p.qty), "avg_cost": float(p.avg_cost_per_share)}
    save_virtual_account(account)
    return account


def init_agent_5k_account() -> VirtualAccount:
    account = VirtualAccount(name="agent_5k", initial_cash=5000.0, cash=5000.0)
    save_virtual_account(account)
    return account


def run_agent_for_account(account_name: str, market_data: dict | None = None, config: dict | None = None) -> dict:
    _ = config
    account = load_virtual_account(account_name)
    syms = sorted(
        set((market_data or {}).keys()) | set(account.positions.keys()) | set(load_opportunity_tickers()[:25])
    )
    qrows = fetch_quotes(syms)
    px_map = {q.symbol.upper(): float(q.px) for q in qrows if float(q.px or 0.0) > 0}
    chg_map = {q.symbol.upper(): float(q.chg_pct or 0.0) for q in qrows}
    trades: list[Trade] = []

    # Simple placeholder rules:
    # - entry: strongest names + positive momentum, cap each new leg at ~12% equity
    # - exit trim: weak momentum <= -6% for held names
    equity_now = _mark_equity(account, px_map)
    if equity_now <= 0:
        equity_now = max(account.cash, 1.0)

    held_syms = set(account.positions.keys())
    for sym in list(held_syms):
        chg = chg_map.get(sym, 0.0)
        px = px_map.get(sym, 0.0)
        if px <= 0:
            continue
        if chg <= -6.0:
            shares = float(account.positions[sym]["shares"]) * 0.35
            if shares > 0:
                account.cash += shares * px
                _update_position(account, sym, -shares, px)
                tr = Trade(ts=utc_now(), symbol=sym, side="SELL", shares=round(shares, 6), price=px, reason="跌幅超阈值减仓")
                account.trades.append(tr)
                trades.append(tr)

    ranked = sorted([(s, chg_map.get(s, 0.0)) for s in syms], key=lambda x: x[1], reverse=True)
    for sym, chg in ranked[:8]:
        if account.cash < 120:
            break
        if sym in account.positions:
            continue
        if chg < 2.0:
            continue
        px = px_map.get(sym, 0.0)
        if px <= 0:
            continue
        budget = min(account.cash, equity_now * 0.12)
        shares = budget / px
        if shares <= 0:
            continue
        account.cash -= shares * px
        _update_position(account, sym, shares, px)
        tr = Trade(ts=utc_now(), symbol=sym, side="BUY", shares=round(shares, 6), price=px, reason="动量入场占位")
        account.trades.append(tr)
        trades.append(tr)

    account.trades = account.trades[-200:]
    eq = _mark_equity(account, px_map)
    save_virtual_account(account)
    return {"account": account.name, "equity": eq, "trades": [asdict(t) for t in trades]}


def compute_account_stats(account: VirtualAccount) -> dict:
    curve = [float(x.get("equity") or 0.0) for x in account.equity_curve]
    latest = curve[-1] if curve else float(account.cash)
    pnl = latest - float(account.initial_cash)
    pnl_pct = (pnl / account.initial_cash * 100.0) if account.initial_cash > 0 else 0.0
    peak = 0.0
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak * 100.0)
    return {
        "total_value": round(latest, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "max_drawdown_approx": round(mdd, 2),
        "trades": len(account.trades),
        "positions": len(account.positions),
    }

